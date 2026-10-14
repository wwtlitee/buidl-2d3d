"""Local factory API client for unattended calls. v0.4.0 / Unclecow.

Uses only the Python standard library. JSON results go to stdout; progress and
errors go to stderr. Interrupting this client leaves submitted jobs in the queue.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'generated-unreviewed', 'failed', 'cancelled', 'interrupted', 'deleted'}
MAX_IMAGE_BYTES = 24 * 1024 * 1024


class ClientError(Exception):
    pass


class TransportError(ClientError):
    def __init__(self, message, refused=False):
        super().__init__(message)
        self.refused = refused


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ClientError('工厂 API 返回了重定向，已拒绝跟随')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ClientError(message)


def emit(value, stream):
    print(json.dumps(value, ensure_ascii=False), file=stream, flush=True)


def service_build():
    from factory_identity import service_fingerprint
    return service_fingerprint(ROOT / 'scripts')


def local_url(value=None, port=None):
    if value is not None and port is not None:
        raise ClientError('--url 和 --port 请只指定一个')
    try:
        parsed = urlsplit(value or f'http://127.0.0.1:{port if port is not None else 7860}')
        selected_port = parsed.port if parsed.port is not None else 7860
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                or not 1 <= selected_port <= 65535):
            raise ValueError()
    except ValueError as error:
        raise ClientError('地址需为本机 http://127.0.0.1:<port>；端口为 1 至 65535') from error
    # Canonicalize localhost to an IP: never consult DNS or environment proxies.
    return f'http://127.0.0.1:{selected_port}', selected_port


class Client:
    def __init__(self, url=None, port=None):
        self.url, self.port = local_url(url, port)
        self.opener = build_opener(ProxyHandler({}), NoRedirects())
        self.health = None

    def request(self, path, payload=None, *, raw=None, filename=None, timeout=10):
        if not path.startswith('/api/'):
            raise ClientError('客户端仅允许工厂 API 路径')
        headers, body = {}, None
        if raw is not None:
            body = raw
            headers.update({'Content-Type': 'application/octet-stream', 'X-File-Name': quote(filename or 'image.png', safe='')})
        elif payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if body is not None:
            headers['X-Factory-Request'] = '1'
        request = Request(self.url + path, data=body, headers=headers, method='POST' if body is not None else 'GET')
        try:
            with self.opener.open(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                detail = json.loads(error.read()).get('error') or str(error)
            except (ValueError, AttributeError):
                detail = str(error)
            raise ClientError(f'HTTP {error.code}: {detail}') from error
        except URLError as error:
            refused = isinstance(error.reason, ConnectionRefusedError) or getattr(error.reason, 'winerror', None) == 10061
            raise TransportError(f'本机工厂服务无法连接：{error.reason}', refused) from error
        except (TimeoutError, OSError) as error:
            raise TransportError(f'本机工厂服务无法连接：{error}') from error
        except ValueError as error:
            raise ClientError('工厂服务返回了无效 JSON') from error
        return result

    def connect(self, ensure_service=False):
        try:
            health = self.request('/api/health', timeout=2)
        except TransportError as error:
            if not ensure_service or not error.refused:
                raise ClientError(f'{error}；可运行 scripts/start-visual-factory.ps1 -NoBrowser -Port {self.port}，或加 --ensure-service') from error
            self.start_service()
            health = self.request('/api/health', timeout=2)
        expected_version = json.loads((ROOT / 'VERSION.json').read_text(encoding='utf-8-sig'))['version']
        if not isinstance(health, dict) or health.get('service') != '2D3DFactory':
            raise ClientError('该本机端口不是 2D3DFactory 服务')
        if health.get('version') != expected_version or health.get('serviceBuild') != service_build():
            raise ClientError('正在运行的工厂版本或代码指纹已过期；请完成当前任务后重启服务。客户端不会自动停止旧服务')
        self.health = health

    def start_service(self):
        portable = ROOT / 'runtime/tools/pwsh/pwsh.exe'
        host = str(portable) if portable.is_file() else shutil.which('pwsh')
        starter = ROOT / 'scripts/start-visual-factory.ps1'
        if not host or not starter.is_file():
            raise ClientError('PowerShell 7 或本机启动脚本缺失，请手动启动工厂服务')
        command = [host, '-NoLogo', '-NoProfile', '-File', str(starter), '-NoBrowser', '-Port', str(self.port)]
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ClientError(f'后台启动服务失败：{error}') from error
        if result.returncode:
            raise ClientError('后台启动服务失败：' + (result.stderr or result.stdout).strip()[-1500:])

    def state(self):
        value = self.request('/api/state')
        if not isinstance(value, dict):
            raise ClientError('工厂状态需为 JSON 对象')
        return value

    def job(self, job_id):
        state = self.state()
        for job in state.get('jobs', []) + state.get('history', []):
            if job.get('id') == job_id:
                return job
        raise ClientError(f'任务不存在或已删除：{job_id}')

    def upload(self, path):
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file() or not 0 < source.stat().st_size <= MAX_IMAGE_BYTES:
            raise ClientError('每张图片需为非空文件，且不超过 24 MB')
        data = source.read_bytes()
        if not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise ClientError('图片文件读取后超出 24 MB')
        return self.request('/api/images', raw=data, filename=source.name)


def wait_jobs(client, jobs, timeout=None):
    job_ids = [job['id'] for job in jobs]
    emit({'event': 'submitted', 'jobs': [{'id': job['id'], 'runId': job.get('runId')} for job in jobs]}, sys.stderr)
    started = time.monotonic()
    while True:
        state = client.state()
        records = {job['id']: job for job in state.get('jobs', []) + state.get('history', [])}
        if any(job_id not in records for job_id in job_ids):
            raise ClientError('已提交任务暂时无法查询或已删除；客户端不会重复提交')
        current = [records[job_id] for job_id in job_ids]
        emit({'event': 'progress', 'jobs': [{key: job.get(key) for key in ('id', 'status', 'phase', 'progress')} for job in current]}, sys.stderr)
        if all(job['status'] in TERMINAL for job in current):
            return current
        if timeout is not None and time.monotonic() - started >= timeout:
            raise ClientError('等待超时；已提交任务继续留在工厂队列中')
        time.sleep(5)


def parser():
    result = Parser(description=__doc__)
    result.add_argument('--url', help='Only local HTTP; defaults to http://127.0.0.1:7860')
    result.add_argument('--port', type=int)
    result.add_argument('--ensure-service', action='store_true', help='Start the hidden service only if the port refuses connections')
    commands = result.add_subparsers(dest='command', required=True, parser_class=Parser)
    for name in ('status', 'tasks'):
        commands.add_parser(name)
    job = commands.add_parser('job')
    job.add_argument('id')
    image = commands.add_parser('image')
    image.add_argument('--engine', default='qwen-image-21')
    image.add_argument('--variant', help='Exact registered weight id; omitted requests retain the Q4_K legacy default')
    image.add_argument('--prompt', required=True)
    image.add_argument('--negative-prompt', default='')
    image.add_argument('--width', type=int, default=512)
    image.add_argument('--height', type=int, default=512)
    image.add_argument('--steps', type=int, default=20)
    image.add_argument('--seed', type=int)
    model = commands.add_parser('model')
    model.add_argument('--name', default='物体')
    model.add_argument('--main', required=True, type=Path)
    for view in ('left', 'back', 'right'):
        model.add_argument('--' + view, type=Path)
    model.add_argument('--engines', required=True, help='Comma-separated engine IDs')
    rig = commands.add_parser('rig', help='Bind an existing factory GLB')
    rig.add_argument('--engine', required=True)
    rig.add_argument('--model-url', required=True)
    rig.add_argument('--name', default='骨骼绑定')
    texture = commands.add_parser('texture', help='Texture an existing unrigged GLB using multiple reference views')
    texture.add_argument('--engine', default='hunyuan-paint')
    texture.add_argument('--model-url', required=True)
    texture.add_argument('--name', default='多图模型上色')
    for view in ('front', 'left', 'back', 'right'):
        texture.add_argument('--' + view, type=Path)
    animation = commands.add_parser('animate', help='Create joint animation on a bound factory GLB')
    animation.add_argument('--engine', default='basic-motion')
    animation.add_argument('--model-url', required=True)
    animation.add_argument('--motion', choices=('joint-test', 'wave', 'walk', 'standard-run'), default='joint-test')
    animation.add_argument('--name', default='制作动作')
    for command in (job, image, model, rig, animation, texture):
        command.add_argument('--wait', action='store_true')
        command.add_argument('--wait-timeout', type=float)
    for action in ('cancel', 'retry', 'delete'):
        command = commands.add_parser(action)
        command.add_argument('--job', required=True)
    command = commands.add_parser('history-delete')
    command.add_argument('--id', required=True)
    return result


def execute(arguments, client):
    from factory_capabilities import require_creation
    require_creation({'stage': {'rig': 'rig', 'animate': 'animation'}.get(arguments.command)})
    if arguments.command == 'status':
        return client.state(), 0
    if arguments.command == 'tasks':
        return client.state().get('resultTasks', []), 0
    if arguments.command in {'cancel', 'retry', 'delete'}:
        if not re.fullmatch(r'[a-f0-9]{32}', arguments.job):
            raise ClientError('队列任务 ID 需为 32 位小写十六进制')
        return client.request(f'/api/jobs/{arguments.job}/{arguments.command}', {}), 0
    if arguments.command == 'history-delete':
        if not re.fullmatch(r'[a-z0-9_-]+~[A-Za-z0-9][A-Za-z0-9_-]{0,127}', arguments.id):
            raise ClientError('历史记录 ID 需为 engine~runId')
        return client.request('/api/history/' + quote(arguments.id, safe='') + '/delete', {}), 0
    if arguments.command == 'job':
        submitted = client.job(arguments.id)
        jobs = [submitted]
    elif arguments.command == 'image':
        payload = {'engineId': arguments.engine, 'prompt': arguments.prompt, 'negativePrompt': arguments.negative_prompt,
                   'width': arguments.width, 'height': arguments.height, 'steps': arguments.steps}
        if arguments.seed is not None:
            payload['seed'] = arguments.seed
        if arguments.variant is not None:
            payload['weightVariant'] = arguments.variant
        submitted = client.request('/api/image-jobs', payload)
        jobs = [submitted]
    elif arguments.command in {'rig', 'animate', 'texture'}:
        payload = {'stage': {'rig': 'rig', 'animate': 'animation', 'texture': 'texture'}[arguments.command], 'engineId': arguments.engine,
                   'modelUrl': arguments.model_url, 'name': arguments.name}
        if arguments.command == 'animate':
            payload['motion'] = arguments.motion
        if arguments.command == 'texture':
            sources = {view: getattr(arguments, view) for view in ('front', 'left', 'back', 'right') if getattr(arguments, view) is not None}
            if sources:
                if 'front' not in sources or not 2 <= len(sources) <= 4:
                    raise ClientError('手动多图上色需2至4图并包含 --front；不指定图片时沿用模型原参考图')
                for path in sources.values():
                    source = path.expanduser().resolve(strict=True)
                    if not source.is_file() or not 0 < source.stat().st_size <= MAX_IMAGE_BYTES:
                        raise ClientError('上色参考图必须为非空文件且不超过24MB')
                payload['images'] = [{'view': view, 'imageId': client.upload(path)['id']} for view, path in sources.items()]
        submitted = client.request('/api/model-jobs', payload)
        jobs = [submitted]
    else:
        selected = [item.strip() for item in arguments.engines.split(',')]
        if not selected or any(not item for item in selected) or len(set(selected)) != len(selected):
            raise ClientError('请指定不重复的工具 ID，以逗号分隔')
        views = {view: getattr(arguments, view) for view in ('main', 'left', 'back', 'right') if getattr(arguments, view) is not None}
        # Validate every source path and size before the first image upload.
        for path in views.values():
            source = path.expanduser().resolve(strict=True)
            if not source.is_file() or not 0 < source.stat().st_size <= MAX_IMAGE_BYTES:
                raise ClientError(f'输入图片不是文件：{path}')
        images = {view: client.upload(path) for view, path in views.items()}
        submitted = client.request('/api/batches', {'name': arguments.name, 'engineIds': selected,
                                  'objects': [{'name': arguments.name, 'mainImageId': images['main']['id'],
                                               'auxiliaryImages': [{'view': view, 'imageId': image['id']} for view, image in images.items() if view != 'main']}]})
        jobs = submitted['jobs']
    if not arguments.wait:
        return submitted, 0
    final = wait_jobs(client, jobs, arguments.wait_timeout)
    success = all(job['status'] == 'generated-unreviewed' for job in final)
    if arguments.command == 'model':
        submitted = {**submitted, 'jobs': final, 'assets': [{**asset, 'jobId': job['id'], 'engine': job['engine']} for job in final for asset in job.get('assets', [])],
                     'status': 'generated-unreviewed' if success else 'failed'}
    else:
        submitted = final[0]
    if not success:
        emit({'error': '任务已结束，但部分任务未生成产物；未自动重试', 'jobs': [{'id': job['id'], 'status': job['status']} for job in final]}, sys.stderr)
    return submitted, 0 if success else 2


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    try:
        arguments = parser().parse_args(argv)
        from factory_capabilities import require_creation
        require_creation({'stage': {'rig': 'rig', 'animate': 'animation'}.get(arguments.command)})
        timeout = getattr(arguments, 'wait_timeout', None)
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ClientError('--wait-timeout 需为大于 0 的有限秒数')
        client = Client(arguments.url, arguments.port)
        client.connect(arguments.ensure_service)
        value, exit_code = execute(arguments, client)
        emit(value, sys.stdout)
        return exit_code
    except KeyboardInterrupt:
        emit({'error': '客户端等待已中止；已提交任务继续留在工厂队列中'}, sys.stderr)
        return 130
    except (ClientError, OSError, ValueError, KeyError) as error:
        emit({'error': str(error)}, sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
