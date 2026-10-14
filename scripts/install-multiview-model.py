"""Install public, pinned Hunyuan3D-2mv weights. v0.1.0 / Unclecow."""
import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'engines' / 'hunyuan3d-mv'
SOURCE = ROOT / 'config' / 'multiview-model-source.json'
REPO = 'tencent/Hunyuan3D-2mv'
REVISION = '3a761b539b29fe4ff64714813aa9560fd66f5de0'
SUBFOLDER = 'hunyuan3d-dit-v2-mv'
WANTED = {'LICENSE', 'NOTICE', 'README.md', 'config.json',
          f'{SUBFOLDER}/config.yaml', f'{SUBFOLDER}/model.fp16.safetensors'}
OUTPUT_LOCK = threading.Lock()


def emit(event):
    with OUTPUT_LOCK:
        print(json.dumps(event, ensure_ascii=False), flush=True)


def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def official_metadata():
    url = f'https://huggingface.co/api/models/{REPO}?blobs=true'
    response = subprocess.run(
        ['curl.exe', '--fail', '--location', '--silent', '--show-error', '--noproxy', '*',
         '--retry', '2', '--connect-timeout', '15', '--max-time', '45', url],
        capture_output=True, check=True, timeout=150, encoding='utf-8', errors='replace')
    data = json.loads(response.stdout)
    if data.get('private') or data.get('gated') or data.get('disabled'):
        raise ValueError('Only public, non-gated, enabled models are allowed')
    if data.get('sha') != REVISION or data.get('id') != REPO:
        raise ValueError('Official repository revision does not match the pinned source')
    selected = [item for item in data['siblings'] if item['rfilename'] in WANTED]
    if {item['rfilename'] for item in selected} != WANTED:
        raise ValueError('Official metadata is missing required model files')
    return {'version': '0.1.0', 'author': 'Unclecow', 'repo': REPO, 'revision': REVISION,
            'metadataUrl': url, 'retrievedAt': timestamp(), 'gated': False, 'private': False,
            'files': selected, 'status': 'downloading', 'receipts': []}


def verify(path, item):
    if path.stat().st_size != item['size']:
        raise ValueError(f'Size mismatch for {item["rfilename"]}')
    sha256 = hashlib.sha256()
    git_sha1 = hashlib.sha1(f'blob {item["size"]}\0'.encode())
    with path.open('rb') as stream:
        while block := stream.read(8 * 1024 * 1024):
            sha256.update(block)
            git_sha1.update(block)
    lfs = item.get('lfs')
    actual = sha256.hexdigest() if lfs else git_sha1.hexdigest()
    expected = lfs['sha256'] if lfs else item['blobId']
    if actual != expected:
        raise ValueError(f'Official hash mismatch for {item["rfilename"]}; retained for inspection')
    return sha256.hexdigest()


def download(item, verify_only=False):
    path = ENGINE / 'models' / item['rfilename']
    record = {'file': item['rfilename'], 'size': item['size'], 'verified': False}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            record['sha256'] = verify(path, item)
            record.update(verified=True, source='verified-local')
            emit(record)
            return record
        if verify_only:
            raise FileNotFoundError(f'Not installed: {item["rfilename"]}')
        partial = path.with_name(path.name + '.part')
        if partial.exists() and partial.stat().st_size == item['size']:
            record['sha256'] = verify(partial, item)
            partial.replace(path)
            record.update(verified=True, source='verified-partial')
            emit(record)
            return record
        if partial.exists() and partial.stat().st_size > item['size']:
            raise ValueError(f'Oversized partial download: {item["rfilename"]}')
        errors = []
        for host in ('hf-mirror.com', 'huggingface.co'):
            url = f'https://{host}/{REPO}/resolve/{REVISION}/{item["rfilename"]}'
            command = ['curl.exe', '--fail', '--location', '--silent', '--show-error',
                       '--noproxy', '*', '--retry', '2', '--retry-delay', '2',
                       '--retry-all-errors', '--connect-timeout', '25', '--max-time', '3600',
                       '--speed-time', '60', '--speed-limit', '1024', '--continue-at', '-',
                       '--output', str(partial), url]
            with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  text=True, encoding='utf-8', errors='replace') as process:
                started = time.monotonic()
                while process.poll() is None:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        downloaded = partial.stat().st_size if partial.exists() else 0
                        emit({'stage': 'download', 'file': item['rfilename'], 'host': host,
                              'bytes': downloaded, 'totalBytes': item['size'],
                              'percent': round(downloaded * 100 / item['size'], 1),
                              'seconds': round(time.monotonic() - started, 1)})
                error = process.stderr.read().strip()
                if process.returncode:
                    errors.append({'host': host, 'exitCode': process.returncode, 'error': error})
                    emit({'stage': 'fallback', 'file': item['rfilename'], 'host': host,
                          'exitCode': process.returncode})
                    continue
            record['sha256'] = verify(partial, item)
            partial.replace(path)
            record.update(verified=True, source=host, attempts=errors)
            break
        if not record['verified']:
            raise RuntimeError(f'Download failed on mirror and official source: {errors}')
    except Exception as error:
        record['error'] = str(error)
    emit(record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--use-saved-metadata', action='store_true',
                        help='Use the official metadata snapshot already saved for the exact public revision')
    args = parser.parse_args()
    if args.verify_only or args.use_saved_metadata:
        metadata = json.loads(SOURCE.read_text(encoding='utf-8'))
        if (metadata.get('repo') != REPO or metadata.get('revision') != REVISION
                or metadata.get('gated') or metadata.get('private')
                or {item['rfilename'] for item in metadata.get('files', [])} != WANTED):
            raise ValueError('Saved source does not match the approved public pin')
    else:
        metadata = official_metadata()
        save_json(SOURCE, metadata)
    files = sorted(metadata['files'], key=lambda item: item['size'], reverse=True)
    emit({'stage': 'start', 'repo': REPO, 'revision': REVISION,
          'totalBytes': sum(item['size'] for item in files), 'files': len(files)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: download(item, args.verify_only), files))
    complete = all(item['verified'] for item in results)
    metadata.update(receipts=results, status='verified' if complete else 'failed', checkedAt=timestamp())
    save_json(SOURCE, metadata)
    manifest_path = ENGINE / 'engine.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if complete:
        if manifest.get('status') != 'ready':
            manifest['status'] = 'installed'
            manifest['notes'] = '官方多视角权重已下载并校验；GPU 生成待验收。'
        manifest['modelRevision'] = REVISION
        manifest['modelSource'] = 'config/multiview-model-source.json'
    else:
        manifest['status'] = 'blocked'
        manifest['notes'] = '官方多视角权重未完成校验，请运行补装脚本查看失败文件。'
    save_json(manifest_path, manifest)
    emit({'stage': 'complete', 'verified': complete, 'engineStatus': manifest['status']})
    raise SystemExit(0 if complete else 1)


if __name__ == '__main__':
    main()
