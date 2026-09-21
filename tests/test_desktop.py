"""Desktop lifecycle tests. No native windows, book imports, or production server are opened."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import importlib.util


ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'desktop' / 'server_host.py'
package_spec = importlib.util.spec_from_file_location('reader_package_runtime', ROOT / 'desktop/package_runtime.py')
packaging = importlib.util.module_from_spec(package_spec)
package_spec.loader.exec_module(packaging)


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for supervisor fixture')


class DesktopSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='reader3-desktop-test-')
        self.project = Path(self.temp.name)
        (self.project / 'server.py').write_text('''
import os, signal, time
from pathlib import Path
def stop(signum, frame):
    Path('stopped').write_text(str(os.getpid()))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
Path('ready').write_text(str(os.getpid()))
while True:
    time.sleep(.05)
''')
        self.processes = []

    def tearDown(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        self.temp.cleanup()

    def start_host(self, parent=None):
        process = subprocess.Popen([
            sys.executable, str(HOST), '--project', str(self.project),
            '--parent', str(os.getpid() if parent is None else parent),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def test_normal_quit_stops_owned_backend(self):
        host = self.start_host()
        wait_for(lambda: (self.project / 'ready').exists())
        child_pid = int((self.project / 'ready').read_text())
        host.terminate()
        self.assertEqual(host.wait(timeout=5), 0)
        self.assertEqual(int((self.project / 'stopped').read_text()), child_pid)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_parent_disappears_then_own_backend_stops(self):
        # A separate driver stands in for the native app. Terminating it must not orphan a server.
        script = '''
import os, subprocess, sys, time
subprocess.Popen([sys.executable, sys.argv[1], '--project', sys.argv[2], '--parent', str(os.getpid())],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while True:
    time.sleep(.05)
'''
        driver = subprocess.Popen([sys.executable, '-c', script, str(HOST), str(self.project)])
        self.processes.append(driver)
        wait_for(lambda: (self.project / 'ready').exists())
        child_pid = int((self.project / 'ready').read_text())
        driver.terminate()
        driver.wait(timeout=3)
        wait_for(lambda: (self.project / 'stopped').exists())
        wait_for(lambda: not self.process_exists(child_pid))
        self.assertEqual(int((self.project / 'stopped').read_text()), child_pid)

    @staticmethod
    def process_exists(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_wrong_parent_never_starts_server(self):
        process = self.start_host(parent=1)
        self.assertEqual(process.wait(timeout=3), 2)
        self.assertFalse((self.project / 'ready').exists())

    def test_backend_failure_is_reported(self):
        (self.project / 'server.py').write_text('raise SystemExit(17)\n')
        process = self.start_host()
        self.assertEqual(process.wait(timeout=3), 17)


class DesktopBundleTests(unittest.TestCase):
    def test_source_packaging_follows_imports_and_excludes_private_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'server.py').write_text('from local_state import store\nimport chapters\n')
            (root / 'local_state.py').write_text('import sqlite3\n')
            (root / 'chapters.py').write_text('from reader3 import Book\n')
            (root / 'reader3.py').write_text('import ebooklib\n')
            (root / 'private_notes.py').write_text('secret = True\n')
            (root / 'my-book.epub').write_bytes(b'private book')
            self.assertEqual([p.name for p in packaging.collect_local_modules(root)],
                             ['chapters.py', 'local_state.py', 'reader3.py', 'server.py'])

    def test_copy_refuses_external_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            outside = root / 'outside.txt'
            outside.write_text('not a runtime file')
            (source / 'link').symlink_to(outside)
            with self.assertRaises(ValueError):
                packaging.checked_copytree(source, root / 'destination')

    def test_copy_resolves_internal_symlinks_without_build_path_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            (source / 'real').write_text('runtime bytes')
            (source / 'link').symlink_to(source / 'real')
            target = root / 'destination'
            packaging.checked_copytree(source, target)
            self.assertEqual((target / 'link').read_text(), 'runtime bytes')
            self.assertFalse((target / 'link').is_symlink())

    def test_native_policy_checks_without_starting_app(self):
        binary = ROOT / 'dist' / 'Reader3.app' / 'Contents' / 'MacOS' / 'Reader3'
        if not binary.exists():
            self.skipTest('Run desktop/build.sh to check the native policies')
        result = subprocess.run([str(binary), '--self-test'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Desktop policy checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
