#!/usr/bin/env python3
"""Build an immutable Windows source release from Git-tracked working-tree files.

Stage new source files before running this script. No network access, launcher
compilation, runtime installation or model inference is performed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "\u542f\u52a8\u9020\u70b9\u5565.vbs",
    "scripts/start-source-factory.ps1",
    "scripts/build-source-release.py",
    "scripts/build-desktop-launcher.ps1",
    "scripts/desktop_launcher.cs",
    "scripts/bootstrap-runtime.ps1",
    "scripts/start-desktop-factory.ps1",
    "config/bootstrap-runtime.json",
    "config/core-requirements.lock",
    "config/desktop-requirements.lock",
    "visual/index.html",
    "VERSION.json",
    "README.md",
    "LICENSE",
}
LOCAL_DIRECTORIES = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".playwright-cli",
    "runtime", "models", "inputs", "outputs", "output", "logs", "pdoc",
    "cache", "dist", "engines", "animation-lab", "texture-lab", "model-lab",
    "credentials",
}
FORBIDDEN_SUFFIXES = {
    ".exe", ".dll", ".pyd", ".pyc", ".pyo", ".safetensors", ".gguf",
    ".ckpt", ".pt", ".pth", ".onnx", ".part", ".7z", ".rar", ".tar",
    ".gz", ".bz2", ".xz",
}
DEVICE_NAME = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
BUNDLE_NAME = re.compile(r"config/deployment-bundles/triposr[^/]*\.zip")
SHA256 = re.compile(r"[0-9a-f]{64}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError("Git command failed: " + detail)
    return result.stdout


def safe_relative_name(name: str) -> str:
    """Reject names that change meaning on Windows or escape a ZIP root."""
    parts = name.split("/")
    if (
        not name or "\\" in name or PurePosixPath(name).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 for character in name)
        or any(character in name for character in ':*?"<>|')
        or any(part.endswith((".", " ")) or DEVICE_NAME.match(part) for part in parts)
    ):
        raise ValueError("Unsafe archive path: " + repr(name))
    return name


def checked_path(name: str) -> Path:
    candidate = ROOT.joinpath(*safe_relative_name(name).split("/"))
    candidate.resolve().relative_to(ROOT)
    current = ROOT
    for part in name.split("/"):
        current = current / part
        information = current.lstat()
        if current.is_symlink() or getattr(information, "st_file_attributes", 0) & 0x400:
            raise ValueError("Symlinks and reparse points cannot be packaged: " + name)
    return candidate


def check_source_name(name: str) -> None:
    safe_relative_name(name)
    parts = name.casefold().split("/")
    if any(part in LOCAL_DIRECTORIES or part == ".env" or part.startswith(".env.") for part in parts):
        raise ValueError("Local data is tracked; remove it from the release index: " + name)
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        raise ValueError("Binary, model or unreviewed archive cannot be released: " + name)
    if suffix == ".zip" and not BUNDLE_NAME.fullmatch(name):
        raise ValueError("Only reviewed TripoSR source bundles may be shipped: " + name)


def read_sources() -> dict[str, bytes]:
    raw_names = git("ls-files", "-z").split(b"\0")
    names = [name.decode("utf-8", errors="strict") for name in raw_names if name]
    if len(names) != len(set(name.casefold() for name in names)):
        raise ValueError("Git contains duplicate or case-colliding paths.")
    missing = REQUIRED - set(names)
    if missing:
        raise ValueError("Required files must exist and be staged: " + ", ".join(sorted(missing)))
    sources = {}
    for name in sorted(names):
        check_source_name(name)
        path = checked_path(name)
        if not path.is_file():
            raise ValueError("Tracked entry is not a regular file: " + name)
        sources[name] = path.read_bytes()
    return sources


def check_bundles(sources: dict[str, bytes]) -> None:
    bundles = [name for name in sources if name.lower().endswith(".zip")]
    if not bundles:
        return
    profiles = json.loads(sources["config/python-profiles.json"].decode("utf-8"))
    approved = {
        bundle["path"]: bundle
        for profile in profiles["profiles"] if profile.get("id") == "triposr"
        for bundle in profile.get("bundles", [])
    }
    for name in bundles:
        pin = approved.get(name, {})
        data = sources[name]
        if pin.get("size") != len(data) or pin.get("sha256") != digest(data):
            raise ValueError("TripoSR source bundle does not match its reviewed pin: " + name)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            seen = set()
            for entry in archive.infolist():
                member = safe_relative_name(entry.filename.rstrip("/"))
                if member.casefold() in seen:
                    raise ValueError("Duplicate path inside source bundle: " + member)
                seen.add(member.casefold())
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("Symlink inside source bundle: " + member)
                if PurePosixPath(member).suffix.lower() in FORBIDDEN_SUFFIXES:
                    raise ValueError("Binary or model inside source bundle: " + member)
            failed = archive.testzip()
            if failed:
                raise ValueError("Source bundle CRC failure: " + failed)


def make_archive(sources: dict[str, bytes], prefix: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sources.items():
            entry = zipfile.ZipInfo(prefix + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data, compresslevel=9)
    data = buffer.getvalue()
    verify_archive(data, sources, prefix)
    return data


def verify_archive(data: bytes, sources: dict[str, bytes], prefix: str) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        expected = {prefix + "/" + name for name in sources}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("Release archive members do not match the tracked source snapshot.")
        if not {prefix + "/" + name for name in REQUIRED}.issubset(expected):
            raise ValueError("Release archive is missing required launch files.")
        failed = archive.testzip()
        if failed:
            raise ValueError("Release archive CRC failure: " + failed)
        for name, original in sources.items():
            if digest(archive.read(prefix + "/" + name)) != digest(original):
                raise ValueError("Release archive content mismatch: " + name)


def write_immutable(path: Path, data: bytes) -> bool:
    """Publish only a complete file; an existing different build is never replaced."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".source-release-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, path)
            return False
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != digest(data):
                raise ValueError("Refusing to overwrite a different release: " + str(path))
            return True
    finally:
        temporary.unlink()


def update_checksums(dist: Path, filename: str, checksum: str) -> None:
    path = dist / "SHA256SUMS.txt"
    checksums = {}
    if path.exists():
        checked_path("dist/SHA256SUMS.txt")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2 or not SHA256.fullmatch(parts[0]):
                raise ValueError("Malformed existing SHA256SUMS.txt; no entries were replaced.")
            safe_relative_name(parts[1])
            if "/" in parts[1] or parts[1] in checksums:
                raise ValueError("Invalid or duplicate SHA256SUMS.txt filename.")
            checksums[parts[1]] = parts[0]
    if filename in checksums and checksums[filename] != checksum:
        raise ValueError("Existing checksum conflicts with this immutable release.")
    checksums[filename] = checksum
    data = "".join(value + "  " + name + "\n" for name, value in sorted(checksums.items()))
    with tempfile.NamedTemporaryFile(dir=dist, prefix=".source-checksums-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    sources = read_sources()
    version = json.loads(sources["VERSION.json"].decode("utf-8"))["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+[a-z]?", version):
        raise ValueError("VERSION.json must contain the complete numeric version and optional lowercase revision.")
    check_bundles(sources)
    prefix = "BUIDL-2D3D-" + version
    filename = prefix + "-Windows-x64.zip"
    archive_data = make_archive(sources, prefix)
    archive_hash = digest(archive_data)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    checked_path("dist")
    archive_path = dist / filename
    reused = write_immutable(archive_path, archive_data)
    verify_archive(archive_path.read_bytes(), sources, prefix)
    receipt_path = dist / (prefix + "-Windows-x64.build.json")
    receipt = {
        "schemaVersion": 1,
        "productVersion": version,
        "artifact": filename,
        "artifactSha256": archive_hash,
        "artifactBytes": len(archive_data),
        "sourceSelection": "git ls-files -z; current working-tree contents",
        "gitCommit": git("rev-parse", "HEAD").decode("ascii").strip(),
        "trackedWorktreeDirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "builtAtUtc": datetime.now(timezone.utc).isoformat(),
        "entryCount": len(sources),
        "archivePrefix": prefix,
        "checks": {"crc": True, "requiredMembers": True, "sourceHashes": True},
        "files": [{"path": name, "bytes": len(data), "sha256": digest(data)} for name, data in sources.items()],
    }
    if receipt_path.exists():
        checked_path(receipt_path.relative_to(ROOT).as_posix())
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        if previous.get("artifactSha256") != archive_hash or previous.get("files") != receipt["files"]:
            raise ValueError("Existing release receipt conflicts with this archive.")
    else:
        write_immutable(receipt_path, (json.dumps(receipt, indent=2, ensure_ascii=True) + "\n").encode("utf-8"))
    update_checksums(dist, filename, archive_hash)
    print(json.dumps({
        "archive": str(archive_path), "sha256": archive_hash,
        "receipt": str(receipt_path), "checksums": str(dist / "SHA256SUMS.txt"),
        "version": version, "files": len(sources), "reused": reused,
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print("Source release failed: " + str(error), file=sys.stderr)
        sys.exit(1)
