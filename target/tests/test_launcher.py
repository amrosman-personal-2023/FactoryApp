"""Tests for the repository-root start.sh launcher.

Every test runs the real start.sh inside a disposable copy of the repository
under the system temp directory, so the launcher never touches the persistent
target/app/orders.db or a .venv in the real checkout. Live tests need
127.0.0.1:8000; they are skipped when something else already listens there,
because the launcher must never take over or stop another listener.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOST, PORT = "127.0.0.1", 8000
BASE_URL = f"http://{HOST}:{PORT}"


def port_in_use():
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex((HOST, PORT)) == 0


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def get(path):
    with urllib.request.urlopen(BASE_URL + path, timeout=5) as resp:
        return resp.status, resp.read().decode()


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return out.stdout.strip() not in ("", "Z") and not out.stdout.strip().startswith("Z")


def start_time(pid):
    out = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    return out.stdout.strip()


class LauncherCase(unittest.TestCase):
    """A disposable checkout with start.sh and the target app (no database, no venv)."""

    def setUp(self):
        # Resolved because start.sh uses the physical path (/private/var on macOS).
        self.tmp = Path(tempfile.mkdtemp(prefix="factoryapp-launcher-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "checkout"
        self.root.mkdir()
        shutil.copy2(REPO / "start.sh", self.root / "start.sh")
        shutil.copytree(REPO / "target", self.root / "target",
                        ignore=shutil.ignore_patterns("__pycache__", "*.db", "tests"))
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()
        self.venv_dir = self.root / ".venv"
        self.pid_file = self.venv_dir / "ordersys-server.pid"
        self.log_file = self.venv_dir / "ordersys-server.log"
        self.addCleanup(self._kill_leftover_servers)

    def launcher(self, *args, timeout=90):
        """Run start.sh by absolute path from a directory outside the checkout."""
        return subprocess.run([str(self.root / "start.sh"), *args], cwd=self.elsewhere,
                              capture_output=True, text=True, timeout=timeout)

    def make_venv(self):
        # A real venv that sees the already-installed pinned packages, so the
        # suite runs offline. The real `-install` path is exercised manually.
        venv.create(self.venv_dir, system_site_packages=True, with_pip=False, symlinks=True)

    def server_pids(self):
        """PIDs of uvicorn processes serving this disposable checkout."""
        marker = f"--app-dir {self.root / 'target'}"
        out = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True)
        pids = []
        for line in out.stdout.splitlines():
            pid, _, command = line.strip().partition(" ")
            if marker in command and "uvicorn" in command:
                pids.append(int(pid))
        return pids

    def recorded_pid(self):
        for line in self.pid_file.read_text().splitlines():
            if line.startswith("pid="):
                return int(line[4:])
        self.fail(f"no pid in {self.pid_file}")

    def write_record(self, pid, started):
        self.venv_dir.mkdir(exist_ok=True)
        self.pid_file.write_text(f"pid={pid}\nstarted={started}\n")

    def spawn_sentinel(self, *cmd):
        proc = subprocess.Popen(list(cmd) or ["sleep", "60"], cwd=self.tmp,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop_sentinel, proc)
        return proc

    @staticmethod
    def _stop_sentinel(proc):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)

    def _kill_leftover_servers(self):
        # Only processes whose command line names this temp checkout are ours.
        for pid in self.server_pids():
            os.kill(pid, signal.SIGKILL)

    def output(self, result):
        return result.stdout + result.stderr


class LauncherOfflineTests(LauncherCase):
    def test_script_is_executable(self):
        self.assertTrue(os.access(REPO / "start.sh", os.X_OK))

    def test_missing_or_unknown_flag_prints_usage_and_fails(self):
        for args in ((), ("-bogus",), ("-start", "-stop")):
            result = self.launcher(*args)
            self.assertNotEqual(result.returncode, 0, args)
            for flag in ("-install", "-start", "-stop"):
                self.assertIn(flag, self.output(result))

    def test_start_without_venv_fails_and_points_to_install(self):
        result = self.launcher("-start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("-install", result.stderr)
        self.assertNotIn("Started", result.stdout)
        self.assertFalse(self.pid_file.exists())
        self.assertFalse(self.venv_dir.exists())
        self.assertEqual(self.server_pids(), [])

    def test_stop_without_record_is_a_noop(self):
        result = self.launcher("-stop")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("not running", result.stdout.lower())

    def test_stop_with_record_of_exited_process_removes_stale_record(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.write_record(gone.pid, "Thu Jan  1 00:00:00 1970")
        result = self.launcher("-stop")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("stale", result.stdout.lower())
        self.assertFalse(self.pid_file.exists())

    def test_stop_never_signals_unrelated_process_with_recorded_pid(self):
        sentinel = self.spawn_sentinel()
        self.assertTrue(wait_for(lambda: start_time(sentinel.pid) != ""))
        # A matching PID plus a matching start time is still not ownership:
        # the process must also be this checkout's uvicorn server.
        for started in ("Thu Jan  1 00:00:00 1970", start_time(sentinel.pid)):
            self.write_record(sentinel.pid, started)
            result = self.launcher("-stop")
            self.assertEqual(result.returncode, 0, self.output(result))
            self.assertIn("not stopping", result.stdout.lower())
            self.assertFalse(self.pid_file.exists())
            time.sleep(0.5)
            self.assertIsNone(sentinel.poll(), "unrelated process was signalled")

    def test_garbage_record_is_treated_as_stale(self):
        self.venv_dir.mkdir()
        self.pid_file.write_text("pid=1; rm -rf /\nstarted=x\n")
        result = self.launcher("-stop")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertFalse(self.pid_file.exists())


@unittest.skipIf(port_in_use(), f"{HOST}:{PORT} is already in use by another process")
class LauncherLiveTests(LauncherCase):
    def setUp(self):
        super().setUp()
        if port_in_use():
            self.skipTest(f"{HOST}:{PORT} is already in use by another process")
        self.make_venv()

    def stop_quietly(self):
        if self.pid_file.exists():
            self.launcher("-stop")

    def test_start_repeat_stop_repeat_and_restart(self):
        self.addCleanup(self.stop_quietly)
        first = self.launcher("-start")
        self.assertEqual(first.returncode, 0, self.output(first))
        self.assertIn(f"{BASE_URL}/customers", first.stdout)
        self.assertEqual(get("/health"), (200, '{"status":"ok"}'))
        self.assertEqual(get("/customers")[0], 200)
        pid = self.recorded_pid()
        self.assertEqual(self.server_pids(), [pid])
        command = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True).stdout
        self.assertIn(f"--host {HOST} --port {PORT}", command)
        self.assertTrue(self.log_file.exists())
        self.assertTrue((self.root / "target" / "app" / "orders.db").exists())

        again = self.launcher("-start")
        self.assertEqual(again.returncode, 0, self.output(again))
        self.assertIn("already running", again.stdout.lower())
        self.assertEqual(self.recorded_pid(), pid)
        self.assertEqual(self.server_pids(), [pid])
        self.assertEqual(get("/health")[0], 200)

        stop = self.launcher("-stop")
        self.assertEqual(stop.returncode, 0, self.output(stop))
        self.assertIn("stopped", stop.stdout.lower())
        self.assertFalse(alive(pid))
        self.assertFalse(self.pid_file.exists())
        self.assertTrue(wait_for(lambda: not port_in_use()))

        stop_again = self.launcher("-stop")
        self.assertEqual(stop_again.returncode, 0, self.output(stop_again))
        self.assertIn("not running", stop_again.stdout.lower())

        restart = self.launcher("-start")
        self.assertEqual(restart.returncode, 0, self.output(restart))
        self.assertEqual(get("/health")[0], 200)
        self.assertNotEqual(self.recorded_pid(), pid)
        final = self.launcher("-stop")
        self.assertEqual(final.returncode, 0, self.output(final))
        self.assertTrue(wait_for(lambda: not port_in_use()))
        self.assertEqual(self.server_pids(), [])

    def test_start_refuses_port_held_by_another_listener(self):
        listener = self.spawn_sentinel(sys.executable, "-m", "http.server", str(PORT),
                                       "--bind", HOST)
        self.assertTrue(wait_for(port_in_use), "sentinel listener did not start")
        result = self.launcher("-start")
        self.assertNotEqual(result.returncode, 0, self.output(result))
        self.assertIn(str(PORT), result.stderr)
        self.assertNotIn("Started", result.stdout)
        self.assertFalse(self.pid_file.exists())
        self.assertEqual(self.server_pids(), [])

        stop = self.launcher("-stop")
        self.assertEqual(stop.returncode, 0, self.output(stop))
        self.assertIsNone(listener.poll(), "sentinel listener was stopped")
        self.assertEqual(get("/")[0], 200)

    def test_startup_failure_is_reported_and_cleaned_up(self):
        (self.root / "target" / "app" / "main.py").write_text(
            'raise RuntimeError("fixture startup failure")\n')
        result = self.launcher("-start")
        self.assertNotEqual(result.returncode, 0, self.output(result))
        self.assertIn("fixture startup failure", result.stderr)
        self.assertIn(str(self.log_file), result.stderr)
        self.assertNotIn("Started", result.stdout)
        self.assertFalse(self.pid_file.exists())
        self.assertEqual(self.server_pids(), [])
        self.assertFalse(port_in_use())

    def test_stale_record_does_not_block_a_fresh_start(self):
        self.addCleanup(self.stop_quietly)
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.write_record(gone.pid, "Thu Jan  1 00:00:00 1970")
        result = self.launcher("-start")
        self.assertEqual(result.returncode, 0, self.output(result))
        self.assertIn("stale", result.stdout.lower())
        self.assertNotEqual(self.recorded_pid(), gone.pid)
        self.assertEqual(get("/health")[0], 200)
        stop = self.launcher("-stop")
        self.assertEqual(stop.returncode, 0, self.output(stop))
        self.assertTrue(wait_for(lambda: not port_in_use()))


if __name__ == "__main__":
    unittest.main()
