"""CPU-only desktop identity contract checks. v0.2.0 / Unclecow.

Windows DLLs are imported by the desktop module, but every window/process API
used by these tests is replaced. Existing desktop windows and services are never
opened, focused, closed, or stopped.
"""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("desktop_identity_fixture", ROOT / "scripts/factory_desktop.py")
desktop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop)


class DesktopIdentityChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="factory-desktop-identity-")
        self.addCleanup(self.directory.cleanup)
        self.pid_file = Path(self.directory.name) / "desktop.pid"
        self.record = {
            "root": str(ROOT), "entrypoint": str((ROOT / "scripts/factory_desktop.py").resolve()),
            "version": "0.2.0", "pid": 12345, "hwnd": 98765, "processBirth": "01dd5305a71743d4",
        }

    def focus(self, record=None, *, birth="01dd5305a71743d4", actual_pid=12345):
        self.pid_file.write_text(json.dumps(self.record if record is None else record), encoding="utf-8")
        self.user, self.kernel = MagicMock(), MagicMock()
        self.user.IsWindow.return_value = True
        self.user.IsWindowVisible.return_value = True
        self.user.IsIconic.return_value = False
        self.user.GetForegroundWindow.return_value = 98765
        self.kernel.GetCurrentThreadId.return_value = 2

        def window_thread(hwnd, process_pointer):
            if process_pointer is not None:
                ctypes.cast(process_pointer, ctypes.POINTER(desktop.wintypes.DWORD)).contents.value = actual_pid
            return 2

        def window_title(hwnd, buffer, length):
            buffer.value = desktop.TITLE_PREFIX + " · v" + self.record["version"]
            return len(buffer.value)

        self.user.GetWindowThreadProcessId.side_effect = window_thread
        self.user.GetWindowTextW.side_effect = window_title
        with ExitStack() as patches:
            patches.enter_context(patch.object(desktop, "PID_FILE", self.pid_file))
            patches.enter_context(patch.object(desktop, "USER", self.user))
            patches.enter_context(patch.object(desktop, "KERNEL", self.kernel))
            patches.enter_context(patch.object(desktop, "product_version", return_value="0.2.0"))
            self.birth_query = patches.enter_context(patch.object(desktop, "process_birth", return_value=birth))
            patches.enter_context(patch.object(desktop.time, "monotonic", side_effect=[0.0, 0.0, 26.0]))
            patches.enter_context(patch.object(desktop.time, "sleep"))
            return desktop.focus_existing()

    def assert_no_window_mutation(self):
        for method in ("ShowWindow", "BringWindowToTop", "SetForegroundWindow", "AttachThreadInput"):
            getattr(self.user, method).assert_not_called()
        self.kernel.ReleaseMutex.assert_not_called()
        self.kernel.CloseHandle.assert_not_called()

    def test_current_identity_returns_record_version_without_rewriting_pid(self):
        result = self.focus()
        self.assertEqual(result, {"status": "foregrounded", "pid": 12345, "hwnd": 98765,
                                  "foreground": True, "visible": True, "version": self.record["version"]})
        self.assertEqual(json.loads(self.pid_file.read_text(encoding="utf-8")), self.record)
        self.user.ShowWindow.assert_called_once_with(98765, 5)
        self.user.SetForegroundWindow.assert_called_once_with(98765)
        self.birth_query.assert_called_once_with(12345)

    def test_old_version_rejected_with_close_window_keep_backend_message(self):
        record = self.record | {"version": "0.1.0"}
        with self.assertRaisesRegex(RuntimeError, "v0.1.0.*v0.2.0.*关闭桌面窗口.*后台生产任务继续"):
            self.focus(record)
        self.assert_no_window_mutation()
        self.assertEqual(json.loads(self.pid_file.read_text(encoding="utf-8")), record)

    def test_foreign_entrypoint_rejected_before_focus(self):
        with self.assertRaisesRegex(RuntimeError, "入口身份不一致"):
            self.focus(self.record | {"entrypoint": str(ROOT / "scripts/other.py")})
        self.assert_no_window_mutation()

    def test_missing_or_empty_birth_never_matches_failed_process_query(self):
        for value in (None, "", 0, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "未停止任何进程"):
                    self.focus(self.record | {"processBirth": value}, birth=None)
                self.birth_query.assert_not_called()
                self.user.IsWindow.assert_not_called()
                self.assert_no_window_mutation()

    def test_failed_birth_query_rejects_verified_record(self):
        with self.assertRaises(RuntimeError):
            self.focus(birth=None)
        self.assert_no_window_mutation()
        self.user.IsWindow.assert_not_called()

    def test_reused_pid_birth_mismatch_rejected(self):
        with self.assertRaises(RuntimeError):
            self.focus(birth="another-process-birth")
        self.assert_no_window_mutation()

    def test_window_belongs_to_other_pid_rejected(self):
        with self.assertRaises(RuntimeError):
            self.focus(actual_pid=99999)
        self.assert_no_window_mutation()

    def test_invalid_root_pid_or_handle_rejected_before_process_query(self):
        for changed in ({"root": str(ROOT.parent)}, {"pid": True}, {"pid": 0}, {"hwnd": True}, {"hwnd": 0}):
            with self.subTest(changed=changed):
                with self.assertRaises(RuntimeError):
                    self.focus(self.record | changed)
                self.birth_query.assert_not_called()
                self.assert_no_window_mutation()

    def test_non_object_pid_record_rejected_without_windows_operations(self):
        with self.assertRaises(RuntimeError):
            self.focus([self.record])
        self.birth_query.assert_not_called()
        self.assert_no_window_mutation()

    def test_duplicate_launcher_keeps_returned_version_without_starting_service(self):
        instance = MagicMock(owner=False)
        returned = {"status": "foregrounded", "version": "record-version", "pid": 12345, "hwnd": 98765}
        status_path = Path(self.directory.name) / "startup.json"
        with patch.object(desktop, "DesktopInstance", return_value=instance), \
                patch.object(desktop, "product_version", return_value="disk-version"), \
                patch.object(desktop, "focus_existing", return_value=returned), \
                patch.object(desktop, "ensure_service") as service, \
                patch.object(desktop, "configure_webview") as webview:
            desktop.run_desktop(7860, status_path)
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8")), returned)
        service.assert_not_called()
        webview.assert_not_called()
        instance.close.assert_called_once()


class DesktopReadyChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="factory-desktop-ready-")
        self.addCleanup(self.directory.cleanup)
        self.status_path = Path(self.directory.name) / 'startup.json'
        self.window = MagicMock()
        self.window.native.Handle.ToInt64.return_value = 98765
        self.window.gui.renderer = 'edgechromium'
        self.instance = MagicMock(owner=True)
        self.user = MagicMock()
        self.user.IsWindow.return_value = True
        self.user.IsWindowVisible.return_value = True
        self.user.IsIconic.return_value = False
        self.pid = desktop.os.getpid()
        self.title = desktop.TITLE_PREFIX + ' · v0.2.0'

        def window_thread(hwnd, process_pointer):
            ctypes.cast(process_pointer, ctypes.POINTER(desktop.wintypes.DWORD)).contents.value = self.pid
            return 2

        def window_title(hwnd, buffer, length):
            buffer.value = self.title
            return len(buffer.value)

        self.user.GetWindowThreadProcessId.side_effect = window_thread
        self.user.GetWindowTextW.side_effect = window_title
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(desktop, 'USER', self.user))
        self.patches.enter_context(patch.object(desktop.time, 'sleep'))
        self.patches.enter_context(patch.object(desktop.time, 'monotonic', side_effect=[0.0, 0.0, 4.0]))

    def publish(self):
        desktop.publish_desktop_ready(self.window, self.instance, self.status_path, '0.2.0')

    def assert_no_ready(self):
        self.assertFalse(self.status_path.exists())
        self.instance.native_ready.assert_not_called()

    def test_visible_own_window_publishes_ready_without_show_or_activation(self):
        self.publish()
        value = json.loads(self.status_path.read_text(encoding='utf-8'))
        self.assertEqual((value['status'], value['hwnd'], value['visible'], value['minimized']), ('ready', 98765, True, False))
        self.user.ShowWindow.assert_not_called()
        self.user.SetForegroundWindow.assert_not_called()
        self.instance.native_ready.assert_called_once_with(self.window)

    def test_hidden_own_window_is_shown_and_read_back_before_ready(self):
        self.user.IsWindowVisible.side_effect = [False, True]
        self.user.ShowWindow.return_value = 0  # Previous hidden state is success-compatible.
        self.publish()
        self.user.ShowWindow.assert_called_once_with(98765, 5)
        self.assertTrue(json.loads(self.status_path.read_text(encoding='utf-8'))['visible'])

    def test_minimized_own_window_is_restored_before_ready(self):
        self.user.IsIconic.side_effect = [True, True, False]
        self.publish()
        self.user.ShowWindow.assert_called_once_with(98765, 9)

    def test_still_hidden_window_cannot_publish_ready(self):
        self.user.IsWindowVisible.return_value = False
        with self.assertRaisesRegex(RuntimeError, '仍不可见.*后台生产任务继续'):
            self.publish()
        self.assert_no_ready()

    def test_invalid_native_handle_is_not_shown(self):
        self.window.native.Handle.ToInt64.return_value = 0
        with self.assertRaisesRegex(RuntimeError, '句柄无效'):
            self.publish()
        self.user.ShowWindow.assert_not_called()
        self.assert_no_ready()

    def test_window_of_another_pid_is_never_shown(self):
        self.pid += 1
        with self.assertRaisesRegex(RuntimeError, '身份不匹配'):
            self.publish()
        self.user.ShowWindow.assert_not_called()
        self.assert_no_ready()

    def test_window_of_another_app_is_never_shown(self):
        self.title = 'Unrelated application'
        with self.assertRaisesRegex(RuntimeError, '身份不匹配'):
            self.publish()
        self.user.ShowWindow.assert_not_called()
        self.assert_no_ready()

    def test_closed_own_window_cannot_publish_ready(self):
        self.user.IsWindow.side_effect = [True, False]
        with self.assertRaisesRegex(RuntimeError, '主窗口已关闭'):
            self.publish()
        self.assert_no_ready()

    def test_wrong_renderer_rejected_before_native_window_calls(self):
        self.window.gui.renderer = 'mshtml'
        with self.assertRaisesRegex(RuntimeError, 'WebView2'):
            self.publish()
        self.user.IsWindow.assert_not_called()
        self.user.ShowWindow.assert_not_called()
        self.assert_no_ready()

    def test_loaded_failure_writes_failed_and_destroys_only_mock_window(self):
        callbacks = []
        self.window.events.loaded.__iadd__.side_effect = lambda callback: callbacks.append(callback) or self.window.events.loaded
        self.user.IsWindowVisible.return_value = False
        webview = MagicMock()
        webview.create_window.return_value = self.window
        webview.start.side_effect = lambda **kwargs: callbacks[0]()
        with patch.object(desktop, 'DesktopInstance', return_value=self.instance), \
                patch.object(desktop, 'product_version', return_value='0.2.0'), \
                patch.object(desktop, 'runtime_version', return_value='fixture'), \
                patch.object(desktop, 'ensure_service') as service, \
                patch.object(desktop, 'set_app_id'), \
                patch.object(desktop, 'configure_webview', return_value=webview):
            desktop.run_desktop(7860, self.status_path)
        value = json.loads(self.status_path.read_text(encoding='utf-8'))
        self.assertEqual(value['status'], 'failed')
        self.assertIn('仍不可见', value['error'])
        self.window.destroy.assert_called_once()
        self.instance.native_ready.assert_not_called()
        self.instance.close.assert_called_once()
        service.assert_called_once_with(7860)  # Mocked: never starts a real service.

    def test_later_loaded_event_does_not_show_or_republish_window(self):
        callbacks = []
        self.window.events.loaded.__iadd__.side_effect = lambda callback: callbacks.append(callback) or self.window.events.loaded
        webview = MagicMock()
        webview.create_window.return_value = self.window

        def loaded_twice(**kwargs):
            callbacks[0]()
            self.user.IsWindowVisible.return_value = False  # User hides/minimizes after first load.
            callbacks[0]()

        webview.start.side_effect = loaded_twice
        with patch.object(desktop, 'DesktopInstance', return_value=self.instance), \
                patch.object(desktop, 'product_version', return_value='0.2.0'), \
                patch.object(desktop, 'runtime_version', return_value='fixture'), \
                patch.object(desktop, 'ensure_service'), \
                patch.object(desktop, 'set_app_id'), \
                patch.object(desktop, 'configure_webview', return_value=webview):
            desktop.run_desktop(7860, self.status_path)
        value = json.loads(self.status_path.read_text(encoding='utf-8'))
        self.assertEqual(value['status'], 'ready')
        self.instance.native_ready.assert_called_once_with(self.window)
        self.user.ShowWindow.assert_not_called()
        self.window.destroy.assert_not_called()

    def test_desktop_closure_stops_its_owned_runtime(self):
        webview=MagicMock()
        webview.create_window.return_value=self.window
        backend=MagicMock(url='http://127.0.0.1:17861/')
        with patch.object(desktop,'DesktopInstance',return_value=self.instance), \
                patch.object(desktop,'runtime_version',return_value='fixture'), \
                patch.object(desktop,'ensure_service',return_value=backend), \
                patch.object(desktop,'set_app_id'), \
                patch.object(desktop,'configure_webview',return_value=webview):
            desktop.run_desktop(17861,self.status_path)
        backend.stop.assert_called_once()
        self.instance.close.assert_called_once()
        self.assertEqual(webview.create_window.call_args.args[1],backend.url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
