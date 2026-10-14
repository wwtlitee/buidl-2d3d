"""Download public, pinned models; verify official HF metadata. v0.0.1 / Unclecow."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
METADATA = json.loads((ROOT / 'config/hf-source-metadata.json').read_text(encoding='utf-8'))
SELECTION = {
    'stabilityai/TripoSR': ('triposr', {'config.yaml', 'model.ckpt', 'README.md'}),
    'tencent/Hunyuan3D-2mini': ('hunyuan3d', {
        'LICENSE', 'NOTICE', 'config.json',
        'hunyuan3d-dit-v2-mini/config.yaml',
        'hunyuan3d-dit-v2-mini/model.fp16.safetensors',
    }),
}

def verify(path, item):
    if path.stat().st_size != item['size']:
        raise ValueError(f'Size mismatch: {path.name}')
    with path.open('rb') as stream:
        if item.get('lfs'):
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            expected = item['lfs']['oid']
        else:
            digest = hashlib.sha1(f'blob {item["size"]}\0'.encode())
            while block := stream.read(1024 * 1024):
                digest.update(block)
            actual, expected = digest.hexdigest(), item['oid']
    if actual != expected:
        raise ValueError(f'Hash mismatch: {path.name}; retained for inspection')
    return actual

def fetch(task):
    repo, engine, item = task
    path = ROOT / 'engines' / engine / 'models' / item['path']
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'repo': repo['repo'], 'revision': repo['sha'], 'path': str(path), 'size': item['size']}
    try:
        if not path.exists():
            partial = path.with_name(path.name + '.part')
            url = f'https://hf-mirror.com/{repo["repo"]}/resolve/{repo["sha"]}/{item["path"]}'
            command = ['curl.exe', '--fail', '--location', '--silent', '--show-error',
                       '--noproxy', '*', '--retry', '3', '--retry-delay', '3',
                       '--retry-all-errors', '--connect-timeout', '25', '--max-time', '3600',
                       '--speed-time', '90', '--speed-limit', '1024', '--continue-at', '-',
                       '--output', str(partial), url]
            subprocess.run(command, check=True, timeout=3900)
            record['hash'] = verify(partial, item)
            partial.rename(path)
        else:
            record['hash'] = verify(path, item)
        record['verified'] = True
    except Exception as error:
        record.update(verified=False, error=str(error))
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return record

def main():
    tasks = []
    for repo in METADATA:
        if repo['repo'] not in SELECTION:
            continue
        if repo.get('gated'):
            raise ValueError('This downloader must not access gated models')
        engine, wanted = SELECTION[repo['repo']]
        selected = [item for item in repo['files'] if item['path'] in wanted]
        if {item['path'] for item in selected} != wanted:
            raise ValueError(f'Missing upstream metadata: {repo["repo"]}')
        tasks.extend((repo, engine, item) for item in selected)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fetch, tasks))
    (ROOT / 'config/public-model-download-results.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if all(result['verified'] for result in results) else 1)

if __name__ == '__main__':
    main()
