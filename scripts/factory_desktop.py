"""Independent WebView2 desktop window. v0.2.0 / Unclecow.

This process owns its internal communication, queues and model subprocesses.
Closing the application stops them and preserves interrupted work for retry.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "logs/desktop.pid"
PROFILE = ROOT / "runtime/desktop/profile"
APP_ID = "Unclecow.BuildSomething.Desktop"
TITLE_PREFIX = "build something · 造点啥"
LOG = logging.getLogger("factory-desktop")
KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
USER = ctypes.WinDLL("user32", use_last_error=True)
KERNEL.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
KERNEL.CreateMutexW.restype = wintypes.HANDLE
KERNEL.ReleaseMutex.argtypes = [wintypes.HANDLE]
KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL.OpenProcess.restype = wintypes.HANDLE
KERNEL.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
KERNEL.GetProcessTimes.restype = wintypes.BOOL
KERNEL.GetCurrentThreadId.restype = wintypes.DWORD
USER.GetForegroundWindow.restype = wintypes.HWND
USER.IsWindow.argtypes = [wintypes.HWND]
USER.IsWindowVisible.argtypes = [wintypes.HWND]
USER.IsIconic.argtypes = [wintypes.HWND]
USER.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
USER.SetForegroundWindow.argtypes = [wintypes.HWND]
USER.BringWindowToTop.argtypes = [wintypes.HWND]
USER.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path | None, value: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def product_version() -> str:
    return str(json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8-sig"))["version"])


def process_birth(pid: int) -> str | None:
    handle = KERNEL.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not KERNEL.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return f"{created.dwHighDateTime:08x}{created.dwLowDateTime:08x}"
    finally:
        KERNEL.CloseHandle(handle)


class DesktopInstance:
    def __init__(self, version: str, pid_file: Path = PID_FILE, namespace: str = "desktop"):
        self.pid_file = pid_file
        key = hashlib.sha256((str(ROOT).casefold() + namespace).encode("utf-8")).hexdigest()[:16]
        ctypes.set_last_error(0)
        self.handle = KERNEL.CreateMutexW(None, True, f"Local\\2D3DFactory.Desktop.{key}")
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.owner = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
        self.record = {"pid": os.getpid(), "processBirth": process_birth(os.getpid()),
                       "root": str(ROOT), "entrypoint": str(Path(__file__).resolve()),
                       "version": version, "hwnd": 0, "startedAt": timestamp()}
        if self.owner:
            save_json(self.pid_file, self.record)

    def native_ready(self, window) -> None:
        self.record["hwnd"] = int(window.native.Handle.ToInt64())
        save_json(self.pid_file, self.record)

    def close(self) -> None:
        if self.owner:
            try:
                record = json.loads(self.pid_file.read_text(encoding="utf-8"))
                if record.get("pid") == os.getpid() and record.get("processBirth") == self.record["processBirth"]:
                    self.pid_file.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            KERNEL.ReleaseMutex(self.handle)
        KERNEL.CloseHandle(self.handle)


def focus_existing() -> dict:
    version = product_version()
    entrypoint = str(Path(__file__).resolve())
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            record = json.loads(PID_FILE.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("桌面身份记录必须为对象")
            pid, hwnd = record.get("pid"), record.get("hwnd")
            birth = record.get("processBirth")
            if (record.get("root") == str(ROOT) and type(pid) is int and pid > 0
                    and type(hwnd) is int and hwnd > 0 and isinstance(birth, str) and birth
                    and birth == process_birth(pid) and USER.IsWindow(hwnd)):
                actual_pid = wintypes.DWORD()
                USER.GetWindowThreadProcessId(hwnd, ctypes.byref(actual_pid))
                title = ctypes.create_unicode_buffer(256)
                USER.GetWindowTextW(hwnd, title, len(title))
                if actual_pid.value == pid and title.value.startswith(TITLE_PREFIX):
                    if record.get("entrypoint") != entrypoint:
                        raise RuntimeError("已运行桌面窗口的入口身份不一致，无法复用；未停止窗口或后台任务。")
                    if record.get("version") != version:
                        raise RuntimeError(f"已运行桌面版本 v{record.get('version', 'unknown')} 与当前 v{version} 不一致；"
                                           "请关闭桌面窗口后重新打开，后台生产任务继续。")
                    USER.ShowWindow(hwnd, 9 if USER.IsIconic(hwnd) else 5)  # Restore minimized windows only.
                    USER.BringWindowToTop(hwnd)
                    USER.SetForegroundWindow(hwnd)
                    foreground_thread = USER.GetWindowThreadProcessId(USER.GetForegroundWindow(), None)
                    current_thread = KERNEL.GetCurrentThreadId()
                    if int(USER.GetForegroundWindow() or 0) != hwnd and foreground_thread and foreground_thread != current_thread:
                        attached = USER.AttachThreadInput(current_thread, foreground_thread, True)
                        try:
                            USER.BringWindowToTop(hwnd)
                            USER.SetForegroundWindow(hwnd)
                        finally:
                            if attached:
                                USER.AttachThreadInput(current_thread, foreground_thread, False)
                    foreground = int(USER.GetForegroundWindow() or 0) == hwnd
                    visible = bool(USER.IsWindowVisible(hwnd)) and not USER.IsIconic(hwnd)
                    if not visible:
                        raise RuntimeError("桌面窗口尚未可见，不能报告已打开。未启动第二个窗口，后台生产任务继续。")
                    if not foreground:
                        raise RuntimeError("桌面窗口已恢复，但系统未授予前台焦点，请从任务栏打开。未启动第二个窗口。")
                    return {"status": "foregrounded", "pid": pid, "hwnd": hwnd,
                            "foreground": foreground, "visible": visible, "version": record["version"]}
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(0.2)
    raise RuntimeError("桌面窗口正在启动或尚未就绪，请稍后再次打开；未停止任何进程。")


def runtime_version() -> str:
    key_id = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    versions = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for flags in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key_id}", 0, winreg.KEY_READ | flags) as key:
                    value = str(winreg.QueryValueEx(key, "pv")[0])
                    numbers = tuple(int(part) for part in value.split("."))
                    if numbers >= (86, 0, 622, 0):
                        versions.append((numbers, value))
            except (OSError, ValueError):
                pass
    if not versions:
        raise RuntimeError("未检测到 Microsoft Edge WebView2 Runtime，请安装官方 WebView2 运行组件后再打开工厂。")
    return max(versions)[1]


def ensure_service(port: int) -> None:
    from factory_app_runtime import LocalAppRuntime, migrate_legacy_service
    try:
        return LocalAppRuntime(ROOT,port).start()
    except RuntimeError:
        if migrate_legacy_service(ROOT,port):
            return LocalAppRuntime(ROOT,port).start()
        raise


def configure_webview():
    import webview
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    webview.settings["ALLOW_FILE_URLS"] = False
    return webview


def set_app_id() -> None:
    setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    setter.argtypes, setter.restype = [wintypes.LPCWSTR], ctypes.c_long
    result = setter(APP_ID)
    if result:
        raise RuntimeError(f"无法设置桌面应用标识：{result}")


def visible_desktop_handle(window) -> int:
    """Verify this process's factory window before showing or publishing ready."""
    hwnd = int(window.native.Handle.ToInt64())
    if hwnd <= 0 or not USER.IsWindow(hwnd):
        raise RuntimeError("桌面主窗口句柄无效，不能报告已打开。")
    actual_pid = wintypes.DWORD()
    USER.GetWindowThreadProcessId(hwnd, ctypes.byref(actual_pid))
    title = ctypes.create_unicode_buffer(256)
    USER.GetWindowTextW(hwnd, title, len(title))
    if actual_pid.value != os.getpid() or not title.value.startswith(TITLE_PREFIX):
        raise RuntimeError("桌面主窗口身份不匹配，未操作该窗口。")
    if not USER.IsWindowVisible(hwnd) or USER.IsIconic(hwnd):
        # STARTUPINFO may suppress the initial WinForms Show(); this is a
        # subsequent explicit call, only for the verified current-process HWND.
        USER.ShowWindow(hwnd, 9 if USER.IsIconic(hwnd) else 5)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not USER.IsWindow(hwnd):
            raise RuntimeError("桌面主窗口已关闭，不能报告已打开。")
        if USER.IsWindowVisible(hwnd) and not USER.IsIconic(hwnd):
            return hwnd
        time.sleep(0.05)
    raise RuntimeError("桌面主窗口仍不可见，不能报告已打开；后台生产任务继续。")


def publish_desktop_ready(window, instance: DesktopInstance, status_path: Path | None, version: str) -> None:
    if window.gui.renderer != "edgechromium":
        raise RuntimeError("桌面未使用 WebView2，已关闭窗口，后台任务继续。")
    hwnd = visible_desktop_handle(window)
    instance.native_ready(window)
    save_json(status_path, {"status": "ready", "version": version, "pid": os.getpid(),
                            "hwnd": hwnd, "visible": True, "minimized": False})


def run_desktop(port: int, status_path: Path | None) -> None:
    version = product_version()
    instance = DesktopInstance(version)
    backend=None
    try:
        if not instance.owner:
            save_json(status_path, focus_existing())
            return
        runtime_version()
        backend=ensure_service(port)
        set_app_id()
        webview = configure_webview()
        from webview.menu import Menu, MenuAction, MenuSeparator
        url = backend.url
        window = webview.create_window(f"{TITLE_PREFIX} · v{version}", url, width=1320, height=880,
                                      min_size=(1060, 740), background_color="#f5f5f7", text_select=True,
                                      focus=True, confirm_close=False)

        startup_finished = threading.Event()

        def loaded():
            if startup_finished.is_set():
                return
            startup_finished.set()
            try:
                publish_desktop_ready(window, instance, status_path, version)
            except Exception as error:
                save_json(status_path, {"status": "failed", "error": str(error)})
                LOG.exception("Desktop main window did not become visible")
                window.destroy()

        def open_outputs():
            os.startfile(str(ROOT / "outputs"))

        menu = [Menu("造点啥", [MenuAction("返回首页", lambda: window.load_url(url)),
                              MenuAction("打开产物目录", open_outputs), MenuSeparator(),
                              MenuAction("关闭应用", window.destroy)])]
        window.events.loaded += loaded
        def closing():
            backend.factory.stopping.set()
            backend.factory.wake.set()
            backend.factory.setup.stopping.set()
            backend.factory.setup.wake.set()
            backend.server.closing.set()
        window.events.closing += closing
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(PROFILE), menu=menu)
    finally:
        try:
            if backend is not None:backend.stop()
        finally:instance.close()


FIXTURE_HTML = b"""<!doctype html><html><head><meta charset='utf-8'><title>Desktop fixture</title></head>
<body><main id='desktop-fixture' data-fixture='isolated'><h1>Native WebView2 fixture</h1>
<canvas id='fixture-canvas' width='16' height='16'></canvas></main></body></html>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE_HTML)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(FIXTURE_HTML)

    def log_message(self, *_):
        pass


def check_application(window, loaded: threading.Event, port: int) -> dict:
    url = f"http://127.0.0.1:{port}/#results"
    loaded.clear()
    window.load_url(url)
    if not loaded.wait(20):
        raise RuntimeError("真实工厂页面未在 20 秒内完成加载")
    window.evaluate_js("""fetch('/api/state', {cache: 'no-store'}).then(response => response.json()).then(state => {
        window.__desktopReadOnlyState = {version: state.version, tasks: (state.resultTasks || []).length};
    }).catch(error => { window.__desktopReadOnlyState = {error: String(error)}; }); void 0;""")
    deadline = time.monotonic() + 20
    application = None
    while time.monotonic() < deadline:
        application = window.evaluate_js("""({title: document.title,
            version: document.querySelector('#product-version')?.textContent,
            taskCardCount: document.querySelectorAll('.result-task-card').length,
            resultsPageVisible: document.querySelector('#results-page')?.hidden === false,
            connection: document.querySelector('#connection-status')?.textContent,
            state: window.__desktopReadOnlyState || null})""")
        api_state = application.get("state") or {}
        if api_state.get("error"):
            raise RuntimeError(api_state["error"])
        if (application.get("resultsPageVisible") and application.get("connection") == "已连接本机"
                and "tasks" in api_state and application["taskCardCount"] == api_state["tasks"]):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("真实桌面任务卡与 API 状态未完成对齐")
    if application["state"]["version"] != product_version() or not application["title"].endswith("v" + product_version()):
        raise RuntimeError("桌面真实页面版本与当前版本不一致")
    application["url"] = url
    application["readOnly"] = True
    preview_found = window.evaluate_js("""(() => {
        const card = [...document.querySelectorAll('.task-primary-assets .asset-card')]
            .find(element => element.querySelector('.asset-type')?.textContent === 'GLB');
        const button = card?.querySelector('[data-action="preview-result"]');
        if (!button) return false;
        button.click(); return true;
    })()""")
    application["glbPreview"] = {"attempted": bool(preview_found), "loaded": False}
    if preview_found:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            preview = window.evaluate_js("""({loaded: document.querySelector('#preview-content model-viewer')?.loaded === true,
                modelUrl: document.querySelector('#preview-content model-viewer')?.getAttribute('src'),
                message: document.querySelector('#preview-message')?.textContent})""")
            if preview["loaded"]:
                application["glbPreview"].update(preview)
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("真实桌面 GLB 预览未完成加载：" + str(preview.get("message", "")))
    return application


def self_test(report_path: Path, verify_app: bool = False, port: int = 7860) -> int:
    started = time.monotonic()
    report = {"version": product_version(), "startedAt": timestamp(), "status": "failed", "mode": "hidden-local-fixture",
              "gpuProductionExecuted": False, "factoryServiceStarted": False, "appUserModelId": APP_ID}
    server = None
    try:
        report["runtime"] = {"webview2Version": runtime_version(), "pywebviewVersion": importlib.metadata.version("pywebview"),
                             "pythonVersion": sys.version.split()[0], "gui": "edgechromium"}
        set_app_id()
        webview = configure_webview()
        report["settings"] = {"downloadsEnabled": webview.settings["ALLOW_DOWNLOADS"],
                              "externalBrowserLinks": webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"],
                              "privateMode": False, "isolatedProfile": True}
        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        fixture_url = f"http://127.0.0.1:{server.server_port}/"
        loaded = threading.Event()
        window = webview.create_window(f"{TITLE_PREFIX} · 隔离桌面验收", fixture_url, width=1320, height=880,
                                      min_size=(1060, 740), hidden=True, focus=False, background_color="#010102")
        window.events.loaded += loaded.set

        def release_fixture_browser():
            # pywebview only disposes this control automatically in private
            # mode. Our persistence check uses a temporary non-private profile.
            window.native.browser.webview.Dispose()

        window.events.closing += release_fixture_browser

        def check_native():
            try:
                if not loaded.wait(25):
                    raise RuntimeError("隐藏 WebView2 窗口未在 25 秒内完成加载")
                if window.gui.renderer != "edgechromium":
                    raise RuntimeError("实际渲染器不是 WebView2，拒绝旧浏览器降级")
                result = window.evaluate_js("""(() => {
                    const canvas = document.querySelector('#fixture-canvas');
                    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
                    const info = gl && gl.getExtension('WEBGL_debug_renderer_info');
                    localStorage.setItem('factory-desktop-fixture', 'persisted');
                    return {domLoaded: document.querySelector('#desktop-fixture')?.dataset.fixture === 'isolated',
                        userAgent: navigator.userAgent, webgl: !!gl, webglVersion: gl && gl.getParameter(gl.VERSION),
                        renderer: info && gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
                        localStorageWritable: localStorage.getItem('factory-desktop-fixture') === 'persisted'};
                })()""")
                report["browser"] = result
                hwnd = int(window.native.Handle.ToInt64())
                report["window"] = {"hidden": True, "focus": False, "hwnd": hwnd,
                                    "visible": bool(USER.IsWindowVisible(hwnd)),
                                    "foreground": int(USER.GetForegroundWindow() or 0) == hwnd}
                loaded.clear()
                window.load_url(fixture_url + "?reload=1")
                if not loaded.wait(15):
                    raise RuntimeError("测试页重新加载超时")
                report["browser"]["draftPersistsAcrossReload"] = window.evaluate_js("localStorage.getItem('factory-desktop-fixture') === 'persisted'")
                required = (result["domLoaded"], result["webgl"], result["localStorageWritable"],
                            report["browser"]["draftPersistsAcrossReload"], not report["window"]["visible"],
                            not report["window"]["foreground"])
                if not all(required):
                    raise RuntimeError("桌面 DOM、WebGL、草稿或隐藏窗口检查未通过")
                if verify_app:
                    report["application"] = check_application(window, loaded, port)
                report["status"] = "passed"
            except Exception as error:
                report["error"] = str(error)
                LOG.exception("Native desktop self-test failed")
            finally:
                save_json(report_path, report)
                window.destroy()

        with tempfile.TemporaryDirectory(prefix="factory-desktop-fixture-") as storage:
            fixture_pid = Path(storage) / "fixture.pid"
            first = DesktopInstance(report["version"], fixture_pid, namespace=storage)
            second = DesktopInstance(report["version"], fixture_pid, namespace=storage)
            try:
                report["singleInstanceGuard"] = first.owner and not second.owner and first.record["processBirth"] is not None
                if not report["singleInstanceGuard"]:
                    raise RuntimeError("隔离桌面单实例检查未通过")
            finally:
                second.close()
                first.close()
            webview.start(check_native, gui="edgechromium", private_mode=False, storage_path=storage)
            time.sleep(0.3)  # Give this disposed fixture's browser process time to release files.
        report["windowClosed"] = not bool(USER.IsWindow(report.get("window", {}).get("hwnd", 0)))
        if not report["windowClosed"]:
            raise RuntimeError("隔离桌面窗口未正常退出")
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        LOG.exception("Desktop self-test could not start")
    finally:
        if server:
            server.shutdown()
            server.server_close()
        report.update(finishedAt=timestamp(), seconds=round(time.monotonic() - started, 3))
        save_json(report_path, report)
    return 0 if report["status"] == "passed" and report.get("windowClosed") else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    log_options = {"level": logging.INFO, "format": "%(asctime)s %(levelname)s %(message)s"}
    if sys.stderr is None:  # pythonw has no console stream.
        log_path = ROOT / "logs/desktop" / f"desktop-{os.getpid()}-error.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_options["handlers"] = [logging.FileHandler(log_path, encoding="utf-8")]
    logging.basicConfig(**log_options)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--startup-status", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify-app", action="store_true", help="self-test 中只读加载已运行的工厂页面，不启动后台")
    parser.add_argument("--report", type=Path, default=ROOT / "logs/desktop-acceptance.json")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口需为 1024 至 65535")
    if args.self_test:
        return self_test(args.report, args.verify_app, args.port)
    try:
        run_desktop(args.port, args.startup_status)
        return 0
    except Exception as error:
        save_json(args.startup_status, {"status": "failed", "error": str(error)})
        LOG.exception("Desktop startup failed; factory service was not stopped")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
