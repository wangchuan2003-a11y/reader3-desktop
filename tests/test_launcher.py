import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import launch_reader


class LauncherTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        python = self.root / '.venv' / 'bin' / 'python'
        python.parent.mkdir(parents=True)
        python.touch()
        root_patch = patch.object(launch_reader, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    @patch('launch_reader.webbrowser.open', return_value=True)
    @patch('launch_reader.subprocess.Popen')
    @patch('launch_reader.server_state', return_value='ours')
    def test_running_reader_opens_shelf_without_second_server(self, state, process, browser):
        self.assertEqual(launch_reader.main(), 0)
        process.assert_not_called()
        browser.assert_called_once_with('http://127.0.0.1:8123')

    @patch('launch_reader.webbrowser.open')
    @patch('launch_reader.subprocess.Popen')
    @patch('launch_reader.server_state', return_value='other')
    def test_unrelated_port_owner_is_not_killed_or_opened(self, state, process, browser):
        self.assertEqual(launch_reader.main(), 1)
        process.assert_not_called()
        browser.assert_not_called()

    @patch('launch_reader.time.sleep')
    @patch('launch_reader.webbrowser.open', return_value=True)
    @patch('launch_reader.subprocess.Popen')
    @patch('launch_reader.server_state', side_effect=['offline', 'offline', 'ours'])
    def test_offline_reader_starts_once_and_opens_only_after_ready(self, state, process, browser, sleep):
        process.return_value.poll.return_value = None

        def open_when_ready(url):
            self.assertEqual(state.call_count, 3)
            return True

        browser.side_effect = open_when_ready
        self.assertEqual(launch_reader.main(), 0)
        process.assert_called_once()
        command = process.call_args.args[0]
        self.assertEqual(command[0], str(self.root / '.venv' / 'bin' / 'python'))
        self.assertEqual(command[-1], str(self.root / 'server.py'))
        self.assertEqual(process.call_args.kwargs['cwd'], self.root)
        self.assertTrue(process.call_args.kwargs['start_new_session'])
        self.assertTrue((self.root / '.reader3' / 'server.log').is_file())
        sleep.assert_called_once()
        browser.assert_called_once_with('http://127.0.0.1:8123')

    @patch('launch_reader.time.sleep')
    @patch('launch_reader.webbrowser.open')
    @patch('launch_reader.subprocess.Popen')
    @patch('launch_reader.server_state', return_value='offline')
    def test_server_exit_reports_failure_without_opening_browser(self, state, process, browser, sleep):
        process.return_value.poll.return_value = 1
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(launch_reader.main(), 1)
        self.assertIn('启动未成功', output.getvalue())
        self.assertIn('.reader3/server.log', output.getvalue())
        process.assert_called_once()
        browser.assert_not_called()
        sleep.assert_not_called()

    @patch('launch_reader.webbrowser.open')
    @patch('launch_reader.subprocess.Popen', side_effect=OSError('cannot start'))
    @patch('launch_reader.server_state', return_value='offline')
    def test_process_creation_failure_has_readable_message(self, state, process, browser):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(launch_reader.main(), 1)
        self.assertIn('未能启动', output.getvalue())
        browser.assert_not_called()

    @patch('launch_reader.time.sleep')
    @patch('launch_reader.webbrowser.open')
    @patch('launch_reader.subprocess.Popen')
    @patch('launch_reader.server_state', return_value='offline')
    def test_readiness_wait_is_bounded_and_does_not_open_unready_server(self, state, process, browser, sleep):
        process.return_value.poll.return_value = None
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(launch_reader.main(), 1)
        self.assertIn('尚未就绪', output.getvalue())
        process.assert_called_once()
        browser.assert_not_called()
        self.assertGreater(sleep.call_count, 0)

    @patch('launch_reader.webbrowser.open')
    @patch('launch_reader.subprocess.Popen')
    @patch('launch_reader.server_state', return_value='offline')
    def test_missing_environment_explains_setup_without_starting_server(self, state, process, browser):
        (self.root / '.venv' / 'bin' / 'python').unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(launch_reader.main(), 1)
        self.assertIn('运行环境不存在', output.getvalue())
        process.assert_not_called()
        browser.assert_not_called()
