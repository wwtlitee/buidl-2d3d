"""In-process runtime owned by the native application window. v0.6.0 / Unclecow."""
from __future__ import annotations

import base64
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time

from factory_queue import Factory
from factory_web import FactoryServer, Handler
from factory_runtime import powershell_command


def _loopback_json(port, path, max_bytes=128 * 1024 * 1024):
    # Direct loopback HTTP deliberately ignores system/environment proxies.
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        client.request('GET', path)
        response = client.getresponse()
        data = response.read(max_bytes + 1)
        if response.status != 200 or len(data) > max_bytes:
            raise ValueError('无法完整核实旧服务状态')
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError('旧服务状态格式无法核实')
        return payload
    finally:
        client.close()


def _idle_legacy_service(port):
    health = _loopback_json(port, '/api/health', max_bytes=64 * 1024)
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[a-z]+|-[a-z]+)?', str(health.get('version', '')))
    if health.get('ok') is not True or health.get('service') != '2D3DFactory' or not match:
        return False
    if tuple(int(number) for number in match.groups()) >= (0, 6, 0):
        return False
    for path, terminal in (
            ('/api/state', {'generated-unreviewed', 'failed', 'cancelled', 'interrupted', 'deleted'}),
            ('/api/setup', {'completed', 'failed', 'cancelled', 'interrupted'})):
        jobs = _loopback_json(port, path).get('jobs')
        if not isinstance(jobs, list) or any(not isinstance(job, dict)
                or job.get('status') not in terminal for job in jobs):
            return False
    return True


def _legacy_listener_owner(root, port, timeout=10):
    command = f"""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$listeners=@(Get-NetTCPConnection -ErrorAction Stop | Where-Object {{ $_.LocalAddress -eq '127.0.0.1' -and $_.LocalPort -eq {port} -and [string]$_.State -eq 'Listen' }} | Sort-Object -Property OwningProcess -Unique)
if($listeners.Count -ne 1){{@{{listenerCount=$listeners.Count}} | ConvertTo-Json -Compress;exit 0}}
$ownerId=[int]$listeners[0].OwningProcess
$entry=Get-CimInstance Win32_Process -Filter ('ProcessId = '+$ownerId)
if($entry){{@{{pid=$ownerId;commandLine=[string]$entry.CommandLine;creationDate=$entry.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress}}
"""
    result = subprocess.run([powershell_command(root), '-NoLogo', '-NoProfile',
        '-NonInteractive', '-WindowStyle', 'Hidden', '-EncodedCommand',
        base64.b64encode(command.encode('utf-16-le')).decode('ascii')],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not result.stdout.strip():
        return None
    owner = json.loads(result.stdout.lstrip('\ufeff'))
    if owner == {'listenerCount': 0}:
        return owner
    if not isinstance(owner, dict) or type(owner.get('pid')) is not int or owner['pid'] <= 0:
        return None
    if not isinstance(owner.get('creationDate'), str) or not owner['creationDate']:
        return None
    return owner


def _windows_arguments(command_line):
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    arguments = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not arguments:
        raise ValueError('无法核实旧服务启动参数')
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))


def _owns_legacy_script(owner, root, port):
    arguments = _windows_arguments(str(owner.get('commandLine', '')))
    if not arguments or Path(arguments[0]).name.lower() not in {'python.exe', 'pythonw.exe'}:
        return False
    script_index = 1
    while script_index < len(arguments) and arguments[script_index] in {'-u', '-B', '-E', '-I', '-s'}:
        script_index += 1
    if script_index >= len(arguments):
        return False
    script = Path(arguments[script_index])
    expected_script = root / 'scripts/factory_web.py'
    if not script.is_absolute() or script.resolve() != expected_script or not expected_script.is_file():
        return False
    options = arguments[script_index + 1:]
    seen = set()
    selected_port = 7860
    while options:
        if len(options) < 2:
            return False
        name, value, *options = options
        if name not in {'--port', '--root'} or name in seen:
            return False
        seen.add(name)
        if name == '--port':
            if not value.isdecimal():
                return False
            selected_port = int(value)
        elif not Path(value).is_absolute() or Path(value).resolve() != root:
            return False
    return selected_port == port


def migrate_legacy_service(root, port):
    """Stop only this project's proven idle pre-0.6 standalone service.

    Unknown owners, changed listeners, active work, and current advanced API
    services are left untouched. Persisted queues are never opened or changed.
    """
    if os.name != 'nt' or type(port) is not int or not 1 <= port <= 65535:
        return False
    root = Path(root).resolve()
    try:
        owner = _legacy_listener_owner(root, port)
        if not owner or not _owns_legacy_script(owner, root, port):
            return False
        if not _idle_legacy_service(port):
            return False
        # Verify listener identity and idle queues again immediately before stop.
        if _legacy_listener_owner(root, port) != owner or not _idle_legacy_service(port):
            return False
        taskkill = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/taskkill.exe')
        result = subprocess.run([taskkill, '/PID', str(owner['pid']), '/T', '/F'],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            current = _legacy_listener_owner(root, port, timeout=remaining)
            if current == {'listenerCount': 0}:
                return True
            if current and current != owner:
                return False
            time.sleep(.1)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError,
            subprocess.SubprocessError, http.client.HTTPException):
        return False
    return False


class AppHandler(Handler):
    """Reject keep-alive requests once the owning window begins to close."""
    def _headers(self, *args, **kwargs):
        # A window-local bridge has no need for persistent client connections.
        # On Windows, closing a socket in another thread does not reliably wake
        # an existing buffered reader until its peer sends or closes.
        self.close_connection = True
        super()._headers(*args, **kwargs)

    def log_error(self, format, *args):
        if format == 'Request timed out: %r':
            return
        super().log_error(format, *args)

    def _dispatch(self, method):
        if self.server.closing.is_set():
            self.close_connection = True
            self._json({'error': '应用正在关闭，请重新打开造点啥后继续。'}, 503)
            return
        method()

    def do_GET(self):
        self._dispatch(super().do_GET)

    def do_POST(self):
        self._dispatch(super().do_POST)


class AppServer(FactoryServer):
    """Own request sockets so closing the app cannot leave idle HTTP threads."""
    daemon_threads = False

    def __init__(self, address):
        self.closing = threading.Event()
        self.request_lock = threading.Lock()
        self.request_sockets = set()
        self.request_threads = set()
        super().__init__(address, None)
        self.RequestHandlerClass = AppHandler

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(2)
        with self.request_lock:
            self.request_sockets.add(request)
        return request, address

    def verify_request(self, request, client_address):
        return not self.closing.is_set()

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self.request_lock:
                self.request_sockets.discard(request)

    def process_request_thread(self, request, client_address):
        thread = threading.current_thread()
        with self.request_lock:
            self.request_threads.add(thread)
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.request_lock:
                self.request_threads.discard(thread)

    def stop_requests(self):
        self.closing.set()
        with self.request_lock:
            requests = list(self.request_sockets)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            request.close()


class LocalAppRuntime:
    """Start and stop local work together with one native app window.

    Bind before opening persisted queues, then hold the same project lease as
    the standalone maintenance entry. Never adopt or stop an external service.
    """
    def __init__(self, root, port=7860, start_worker=True):
        self.root = Path(root).resolve()
        self.port = port
        self.start_worker = start_worker
        self.url = f'http://127.0.0.1:{port}/'
        self.factory = None
        self.server = None
        self.thread = None
        self._lease = None
        self._lock = threading.RLock()
        self._stopped = False

    def _acquire_lease(self):
        lock_file = self.root / 'logs/visual-factory/service.lock'
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lease = lock_file.open('a+b')
        try:
            if lease.seek(0, 2) == 0:
                lease.write(b'1')
                lease.flush()
            lease.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lease.close()
            raise RuntimeError('这个项目已在其他入口运行。请关闭旧版窗口或旧服务后，再打开造点啥。') from error
        self._lease = lease

    def start(self):
        with self._lock:
            if self._stopped:
                raise RuntimeError('此应用已经关闭，请重新打开造点啥。')
            if self.thread and self.thread.is_alive():
                return self
            try:
                self.server = AppServer(('127.0.0.1', self.port))
            except OSError as error:
                raise RuntimeError('应用内部端口已被占用。请关闭旧版窗口或旧服务后，再打开造点啥。') from error
            try:
                self._acquire_lease()
                self.factory = Factory(self.root, start_worker=self.start_worker)
                self.server.factory = self.factory
                self.port = self.server.server_port
                self.url = f'http://127.0.0.1:{self.port}/'
                self.thread = threading.Thread(target=self.server.serve_forever,
                    kwargs={'poll_interval': .1}, name='build-something-local-runtime', daemon=True)
                self.thread.start()
            except Exception:
                self.server.server_close()
                if self.factory:
                    self.factory.close(pause_pending=True)
                if self._lease:
                    self._lease.close()
                    self._lease = None
                raise
        return self

    def stop(self):
        with self._lock:
            if self._stopped:
                return
            errors = []
            if self.factory:
                # Signal both workers immediately, before waiting on HTTP reads.
                self.factory.stopping.set()
                self.factory.wake.set()
                self.factory.setup.stopping.set()
                self.factory.setup.wake.set()
            if self.server:
                try:
                    self.server.stop_requests()
                    if self.thread and self.thread.is_alive():
                        self.server.shutdown()
                    self.server.server_close()
                    if self.thread:
                        self.thread.join(timeout=5)
                        if self.thread.is_alive():
                            raise RuntimeError('应用内部通信仍在停止，请重试关闭。')
                except Exception as error:
                    errors.append(error)
            if self.factory:
                try:
                    self.factory.close(pause_pending=True)
                except Exception as error:
                    errors.append(error)
            if errors:
                # Keep ownership until cleanup actually completes. A finally
                # callback can retry instead of permitting another app to load
                # and recover a queue whose worker has not yet stopped.
                raise errors[0]
            if self._lease:
                self._lease.close()
                self._lease = None
            self._stopped = True

    close = stop

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()
