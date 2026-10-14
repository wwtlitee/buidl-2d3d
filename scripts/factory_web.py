"""Loopback web application for the 2D-to-3D factory. v0.4.0 / Unclecow."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import threading
from urllib.parse import unquote, urlparse

from factory_queue import Factory, safe_child

ROOT = Path(__file__).resolve().parents[1]


class FactoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, factory):
        self.factory = factory
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            # Normal tab closure can also abort the keep-alive request reader.
            pass

    @property
    def factory(self):
        return self.server.factory

    def _check_host(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            raise PermissionError("只允许从本机工厂地址访问")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + address for address in allowed}:
            raise PermissionError("跨站请求已拒绝")

    def _headers(self, status, mime, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def _file(self, path, download=False):
        if not path.is_file():
            raise FileNotFoundError("文件不存在")
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        extra = {"Accept-Ranges": "bytes"}
        if download:
            from urllib.parse import quote
            extra["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(path.name)
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()) or size == 0:
                self._headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                return
            first, last = match.groups()
            start = int(first) if first else max(0, size - int(last))
            end = min(size - 1, int(last)) if first and last else size - 1
            if start > end or start >= size:
                self._headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                return
            status = 206
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        mime = {".glb": "model/gltf-binary", ".js": "text/javascript", ".obj": "text/plain", ".ply": "application/octet-stream"}.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        length = end - start + 1
        self._headers(status, mime, max(0, length), extra)
        if self.command != "HEAD":
            with path.open("rb") as stream:
                stream.seek(start)
                while length > 0:
                    block = stream.read(min(length, 1024 * 1024))
                    if not block:
                        break
                    self.wfile.write(block)
                    length -= len(block)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._check_host()
            path = unquote(urlparse(self.path).path)
            if path == "/api/health":
                self._json({"ok": True, "version": self.factory.version(), "service": "2D3DFactory", "product": "build something", "displayName": "造点啥", "serviceBuild": self.factory.service_build})
            elif path == "/favicon.ico":
                self._headers(204, "image/x-icon", 0)
            elif path == "/api/state":
                self._json(self.factory.state())
            elif path == "/api/setup":
                self._json(self.factory.setup.snapshot())
            elif path == "/api/samples":
                self._json(self.factory.samples())
            elif path.startswith("/files/"):
                relative = path[len("/files/"):]
                bucket, _, remainder = relative.partition("/")
                if bucket not in {"inputs", "outputs"}:
                    raise PermissionError("此文件不属于输入或产物")
                try:
                    file_path = safe_child(self.factory.root / bucket, remainder)
                except ValueError as error:
                    raise PermissionError("文件路径超出输入或产物目录") from error
                self._file(file_path, urlparse(self.path).query == "download=1")
            elif match := re.fullmatch(r"/api/(batches|history)/([^/]+)/download", path):
                arguments = {"batch_id" if match[1] == "batches" else "history_id": match[2]}
                archive = self.factory.archive(**arguments)
                try:
                    self._file(archive, True)
                finally:
                    archive.unlink(missing_ok=True)
            elif match := re.fullmatch(r"/api/result-tasks/([^/]+)/download", path):
                archive = self.factory.archive(task_id=match[1])
                try:
                    self._file(archive, True)
                finally:
                    archive.unlink(missing_ok=True)
            elif path.startswith("/api/"):
                raise FileNotFoundError("接口不存在")
            else:
                static = path.lstrip("/") or "index.html"
                self._file(safe_child(self.factory.root / "visual", static))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as error:
            self._error(error)

    def do_POST(self):
        try:
            self._check_host()
            if self.headers.get("X-Factory-Request") != "1":
                raise PermissionError("缺少本地工厂请求标识")
            path = unquote(urlparse(self.path).path)
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("不支持分块上传")
            length = int(self.headers.get("Content-Length", "0"))
            limit = 512 * 1024 * 1024 if path == "/api/models" else 256*1024*1024 if path=='/api/media-inputs' else 24 * 1024 * 1024 if path == "/api/images" else 1024 * 1024
            if not 0 <= length <= limit:
                raise ValueError("请求过大，请减少图片大小或物体数量")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("上传未完成，请重试")
            if path == "/api/images":
                self._json(self.factory.upload_image(body, unquote(self.headers.get("X-File-Name", "图片"))), 201)
                return
            if path == "/api/models":
                self._json(self.factory.upload_model(body, unquote(self.headers.get('X-File-Name','model.glb'))),201)
                return
            if path=='/api/media-inputs':
                self._json(self.factory.upload_media_input(body,unquote(self.headers.get('X-File-Name',''))),201)
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("生产请求需使用 JSON")
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求内容需为 JSON 对象")
            if path == "/api/pipelines":
                self._json(self.factory.create_pipeline(payload),201)
            elif match := re.fullmatch(r'/api/pipelines/([a-f0-9]{32})/(cancel|retry)',path):
                self._json(self.factory.cancel_pipeline(match[1]) if match[2]=='cancel' else self.factory.retry_pipeline(match[1]),200 if match[2]=='cancel' else 201)
            elif path == "/api/setup/install":
                self._json(self.factory.install_model(payload), 202)
            elif path=='/api/setup/credentials':
                self._json(self.factory.setup.configure_credentials(payload))
            elif path == "/api/setup/refresh":
                self._json(self.factory.setup.snapshot(refresh=True))
            elif match := re.fullmatch(r"/api/setup/jobs/([a-f0-9]{32})/(cancel|retry)", path):
                self._json(self.factory.setup.cancel(match[1]) if match[2] == 'cancel' else self.factory.install_model({},retry_id=match[1]))
            elif path == "/api/batches":
                self._json(self.factory.create_batch(payload), 201)
            elif path == "/api/image-jobs":
                self._json(self.factory.create_image_job(payload), 201)
            elif path=='/api/media-jobs':
                self._json(self.factory.create_media_job(payload),201)
            elif path == "/api/model-jobs":
                self._json(self.factory.create_model_job(payload), 201)
            elif path == "/api/images/from-output":
                self._json(self.factory.import_output_image(payload.get("url")), 201)
            elif match := re.fullmatch(r"/api/(?:jobs|image-jobs)/([a-f0-9]{32})/(cancel|retry|delete)", path):
                method = {"cancel": self.factory.cancel_job, "retry": self.factory.retry_job,
                          "delete": self.factory.delete_job}[match[2]]
                self._json(method(match[1]))
            elif match := re.fullmatch(r"/api/history/([a-z0-9_-]+~[a-zA-Z0-9_-]+)/delete", path):
                self._json(self.factory.delete_history(match[1]))
            elif match := re.fullmatch(r"/api/batches/([a-f0-9]{32})/cancel", path):
                self._json(self.factory.cancel_batch(match[1]))
            else:
                raise FileNotFoundError("接口不存在")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as error:
            self._error(error)

    def _error(self, error):
        status = 403 if isinstance(error, PermissionError) else 404 if isinstance(error, FileNotFoundError) else 400 if isinstance(error, (ValueError, TypeError, KeyError)) else 500
        if self.command == "POST":
            # Invalid body lengths must not become a second request on keep-alive.
            self.close_connection = True
        self._json({"error": str(error)}, status)


def main():
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    # Bind first: a second start must not read/recover an active queue.
    server = FactoryServer(("127.0.0.1", args.port), None)
    factory = None
    service_lock = None
    try:
        if os.name == "nt":
            import msvcrt
            lock_file = args.root / "logs/visual-factory/service.lock"
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            service_lock = lock_file.open("a+b")
            if service_lock.seek(0, 2) == 0:
                service_lock.write(b"1")
                service_lock.flush()
            service_lock.seek(0)
            msvcrt.locking(service_lock.fileno(), msvcrt.LK_NBLCK, 1)
        factory = Factory(args.root)
        server.factory = factory
    except Exception:
        server.server_close()
        if service_lock:
            service_lock.close()
        raise
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"build something · 造点啥 v{factory.version()} · http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        factory.close()
        if service_lock:
            service_lock.close()


if __name__ == "__main__":
    main()
