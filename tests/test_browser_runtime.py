from __future__ import annotations

import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, call, patch

from invoice_manager.services import browser_runtime


MISSING = RuntimeError(
    "BrowserType.launch: Executable doesn't exist at C:\\missing\\chrome-headless-shell.exe\n"
    "Please run playwright install"
)


class _Playwright:
    def __init__(self, launch: Mock):
        self.chromium = Mock()
        self.chromium.launch = launch


class BrowserRuntimeTests(unittest.TestCase):
    def test_existing_bundled_browser_is_always_preferred(self) -> None:
        browser = object()
        launch = Mock(return_value=browser)
        playwright = _Playwright(launch)

        with patch.object(browser_runtime, "_edge_executable") as edge, patch.object(
            browser_runtime, "_install_headless_shell"
        ) as install:
            self.assertIs(browser_runtime.launch_browser(playwright), browser)

        launch.assert_called_once_with(headless=True)
        edge.assert_not_called()
        install.assert_not_called()

    def test_missing_bundled_browser_uses_existing_edge(self) -> None:
        edge_browser = object()
        launch = Mock(side_effect=[MISSING, edge_browser])
        playwright = _Playwright(launch)
        progress = Mock()

        with patch.object(browser_runtime.sys, "platform", "win32"), patch.object(
            browser_runtime, "_edge_executable", return_value=Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
        ), patch.object(browser_runtime, "_install_headless_shell") as install:
            self.assertIs(browser_runtime.launch_browser(playwright, progress), edge_browser)

        self.assertEqual(launch.call_args_list, [call(headless=True), call(channel="msedge", headless=True)])
        progress.assert_called_once_with("WindowsのMicrosoft Edgeを使用します。")
        install.assert_not_called()

    def test_edge_launch_failure_does_not_download_another_browser(self) -> None:
        launch = Mock(side_effect=[MISSING, RuntimeError("organization policy: private detail")])
        playwright = _Playwright(launch)

        with patch.object(browser_runtime.sys, "platform", "win32"), patch.object(
            browser_runtime, "_edge_executable", return_value=Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")
        ), patch.object(browser_runtime, "_install_headless_shell") as install:
            with self.assertRaisesRegex(browser_runtime.BrowserRuntimeError, "Microsoft Edge") as raised:
                browser_runtime.launch_browser(playwright)

        self.assertNotIn("private detail", str(raised.exception))
        install.assert_not_called()

    def test_non_missing_launch_failure_never_enters_fallback(self) -> None:
        launch = Mock(side_effect=RuntimeError("browser was blocked by policy"))
        playwright = _Playwright(launch)

        with patch.object(browser_runtime, "_edge_executable") as edge, patch.object(
            browser_runtime, "_install_headless_shell"
        ) as install:
            with self.assertRaisesRegex(browser_runtime.BrowserRuntimeError, "同梱ブラウザー"):
                browser_runtime.launch_browser(playwright)

        edge.assert_not_called()
        install.assert_not_called()

    def test_edge_absent_downloads_once_under_lock_then_launches(self) -> None:
        installed_browser = object()
        launch = Mock(side_effect=[MISSING, MISSING, installed_browser])
        playwright = _Playwright(launch)

        with patch.object(browser_runtime.sys, "platform", "win32"), patch.object(
            browser_runtime, "_edge_executable", return_value=None
        ), patch.object(browser_runtime, "_browser_root", return_value=Path("C:/portable/runtime/browsers")), patch.object(
            browser_runtime, "_browser_install_lock", return_value=nullcontext()
        ) as lock, patch.object(browser_runtime, "_install_headless_shell") as install:
            self.assertIs(browser_runtime.launch_browser(playwright), installed_browser)

        lock.assert_called_once_with(Path("C:/portable/runtime/browsers/.digitalbuilder-install.lock"))
        install.assert_called_once()
        self.assertEqual(launch.call_count, 3)

    def test_waiting_process_rechecks_browser_before_downloading(self) -> None:
        installed_browser = object()
        launch = Mock(side_effect=[MISSING, installed_browser])
        playwright = _Playwright(launch)

        with patch.object(browser_runtime.sys, "platform", "win32"), patch.object(
            browser_runtime, "_edge_executable", return_value=None
        ), patch.object(browser_runtime, "_browser_install_lock", return_value=nullcontext()), patch.object(
            browser_runtime, "_install_headless_shell"
        ) as install:
            self.assertIs(browser_runtime.launch_browser(playwright), installed_browser)

        install.assert_not_called()

    def test_installer_uses_current_python_without_shell_and_normal_tls(self) -> None:
        progress = Mock()
        completed = subprocess.CompletedProcess([], 0, "ok")
        with patch.dict(
            browser_runtime.os.environ,
            {"NODE_TLS_REJECT_UNAUTHORIZED": "0", "PLAYWRIGHT_BROWSERS_PATH": "C:/portable/runtime/browsers"},
            clear=False,
        ), patch.object(browser_runtime.subprocess, "run", return_value=completed) as run:
            browser_runtime._install_headless_shell(progress)

        command = run.call_args.args[0]
        self.assertEqual(command[0], browser_runtime.sys.executable)
        self.assertEqual(command[-5:], ["-m", "playwright", "install", "chromium", "--only-shell"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], browser_runtime.INSTALL_TIMEOUT_SECONDS)
        self.assertNotIn("NODE_TLS_REJECT_UNAUTHORIZED", run.call_args.kwargs["env"])
        self.assertEqual(
            [item.args[0] for item in progress.call_args_list],
            ["ブラウザー実行環境を初回ダウンロードしています…", "ブラウザー実行環境の準備が完了しました。"],
        )

    def test_failed_installer_does_not_expose_subprocess_output(self) -> None:
        completed = subprocess.CompletedProcess([], 1, "secret download response")
        with patch.object(browser_runtime.subprocess, "run", return_value=completed):
            with self.assertRaises(browser_runtime.BrowserRuntimeError) as raised:
                browser_runtime._install_headless_shell(lambda _message: None)
        self.assertNotIn("secret", str(raised.exception))

    def test_edge_detection_requires_a_real_executable_file(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            local = Path(folder)
            executable = local / "Microsoft/Edge/Application/msedge.exe"
            executable.parent.mkdir(parents=True)
            self.assertIsNone(browser_runtime._edge_executable({"LOCALAPPDATA": str(local)}))
            executable.write_bytes(b"MZ")
            self.assertEqual(browser_runtime._edge_executable({"LOCALAPPDATA": str(local)}), executable)


if __name__ == "__main__":
    unittest.main()
