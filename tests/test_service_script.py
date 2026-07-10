from __future__ import annotations

import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "service.sh"


class ServiceScriptTests(unittest.TestCase):
    def run_script(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SERVICE_SCRIPT), *args],
            cwd=cwd or REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    def isolated_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for name in (
            "COGNITRADE_API_TOKEN",
            "COGNITRADE_API_ALLOW_UNAUTHENTICATED",
            "COGNITRADE_API_HOST",
            "COGNITRADE_API_PORT",
            "COGNITRADE_API_RELOAD",
            "COGNITRADE_RUNTIME_DIR",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
        ):
            env.pop(name, None)
        env["COGNITRADE_API_TOKEN"] = ""
        env["OPENAI_API_KEY"] = ""
        env["OPENAI_BASE_URL"] = ""
        env["COGNITRADE_PYTHON_BIN"] = sys.executable
        return env

    @staticmethod
    def free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def test_script_is_executable_and_has_valid_bash_syntax(self) -> None:
        self.assertTrue(os.access(SERVICE_SCRIPT, os.X_OK))
        result = subprocess.run(
            ["bash", "-n", str(SERVICE_SCRIPT)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_help_works_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_script("--help", cwd=Path(temp_dir))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--local-unauthenticated", result.stdout)

    def test_conflicting_service_scopes_are_rejected(self) -> None:
        result = self.run_script("check", "--backend-only", "--frontend-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutually exclusive", result.stdout)

    def test_backend_preflight_works_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir, tempfile.TemporaryDirectory() as cwd:
            result = self.run_script(
                "check",
                "--backend-only",
                "--install",
                "never",
                "--local-unauthenticated",
                "--backend-host",
                "127.0.0.1",
                "--backend-port",
                str(self.free_port()),
                "--runtime-dir",
                runtime_dir,
                cwd=Path(cwd),
                env=self.isolated_env(),
            )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("preflight passed", result.stdout)

    def test_backend_preflight_detects_occupied_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = int(listener.getsockname()[1])
            with tempfile.TemporaryDirectory() as runtime_dir:
                result = self.run_script(
                    "check",
                    "--backend-only",
                    "--install",
                    "never",
                    "--local-unauthenticated",
                    "--backend-host",
                    "127.0.0.1",
                    "--backend-port",
                    str(port),
                    "--runtime-dir",
                    runtime_dir,
                    env=self.isolated_env(),
                )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already in use", result.stdout)

    def test_frontend_preflight_when_node_runtime_is_available(self) -> None:
        vite = REPO_ROOT / "frontend" / "node_modules" / ".bin" / "vite"
        if shutil.which("node") is None or shutil.which("npm") is None or not vite.exists():
            self.skipTest("frontend runtime is not installed")
        version_check = subprocess.run(
            [
                "node",
                "-e",
                "const [a,b]=process.versions.node.split('.').map(Number);"
                "process.exit(((a===20&&b>=19)||(a===22&&b>=12)||a>22)?0:1)",
            ],
            check=False,
        )
        if version_check.returncode != 0:
            self.skipTest("installed Node.js does not satisfy Vite")

        with tempfile.TemporaryDirectory() as runtime_dir:
            result = self.run_script(
                "check",
                "--frontend-only",
                "--install",
                "never",
                "--frontend-port",
                str(self.free_port()),
                "--runtime-dir",
                runtime_dir,
                env=self.isolated_env(),
            )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("preflight passed", result.stdout)

    def test_stop_interrupts_dependency_process_group_and_project_lock_is_global(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        version_check = subprocess.run(
            [
                "node",
                "-e",
                "const [a,b]=process.versions.node.split('.').map(Number);"
                "process.exit(((a===20&&b>=19)||(a===22&&b>=12)||a>22)?0:1)",
            ],
            check=False,
        )
        if version_check.returncode != 0:
            self.skipTest("installed Node.js does not satisfy Vite")

        process: subprocess.Popen[str] | None = None
        active_pid = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            marker = temp_path / "npm.pid"
            child_marker = temp_path / "npm-child.pid"
            fake_npm = bin_dir / "npm"
            fake_npm.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$$\" > \"$COGNITRADE_TEST_NPM_PID\"\n"
                "sleep 60 &\n"
                "child=$!\n"
                "printf '%s\\n' \"$child\" > \"$COGNITRADE_TEST_NPM_CHILD_PID\"\n"
                "wait \"$child\"\n",
                encoding="utf-8",
            )
            fake_npm.chmod(0o755)

            runtime_dir = temp_path / "runtime"
            other_runtime_dir = temp_path / "other-runtime"
            env = self.isolated_env()
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
            env["COGNITRADE_TEST_NPM_PID"] = str(marker)
            env["COGNITRADE_TEST_NPM_CHILD_PID"] = str(child_marker)
            try:
                process = subprocess.Popen(
                    [
                        str(SERVICE_SCRIPT),
                        "start",
                        "--frontend-only",
                        "--install",
                        "always",
                        "--runtime-dir",
                        str(runtime_dir),
                        "--shutdown-timeout",
                        "3",
                    ],
                    cwd=REPO_ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                deadline = time.monotonic() + 15
                while (
                    (not marker.exists() or not child_marker.exists())
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                self.assertTrue(marker.exists(), process.communicate(timeout=2)[0] if process.poll() is not None else "")
                self.assertTrue(child_marker.exists())
                active_pid = int(marker.read_text(encoding="utf-8").strip())
                child_pid = int(child_marker.read_text(encoding="utf-8").strip())

                competing = self.run_script(
                    "check",
                    "--frontend-only",
                    "--install",
                    "never",
                    "--runtime-dir",
                    str(other_runtime_dir),
                    env=env,
                )
                self.assertNotEqual(competing.returncode, 0)
                self.assertIn("another Cognitrade service operation", competing.stdout)

                stop_env = env.copy()
                stop_env["COGNITRADE_RUNTIME_DIR"] = str(runtime_dir)
                stopped = self.run_script("stop", env=stop_env, timeout=15)
                output, _ = process.communicate(timeout=10)
                self.assertEqual(stopped.returncode, 0, stopped.stdout)
                self.assertIn("stopping frontend dependency installation", output)
                for pid in (active_pid, child_pid):
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                self.assertFalse((runtime_dir / "lock").exists())
                project_lock = REPO_ROOT / ".runtime" / "operation.lock"
                if project_lock.exists():
                    self.assertNotEqual(
                        int(project_lock.read_text(encoding="utf-8").splitlines()[0]),
                        process.pid,
                    )
            finally:
                if not active_pid:
                    for candidate in (marker, runtime_dir / "lock" / "active.pid"):
                        try:
                            active_pid = int(candidate.read_text(encoding="utf-8").strip())
                            break
                        except (FileNotFoundError, ValueError):
                            continue
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                if active_pid:
                    try:
                        os.killpg(active_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def test_stale_custom_runtime_refuses_to_forget_a_live_orphan(self) -> None:
        orphan: subprocess.Popen[bytes] | None = None
        project_lock = REPO_ROOT / ".runtime" / "operation.lock"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runtime_dir = temp_path / "runtime"
            lock_dir = runtime_dir / "lock"
            marker = temp_path / "orphan.ready"
            lock_dir.mkdir(parents=True)
            try:
                orphan = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "import os, pathlib, sys, time; "
                        "os.setsid(); pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(60)",
                        str(marker),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 5
                while not marker.exists() and orphan.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(marker.exists())
                (lock_dir / "supervisor.pid").write_text("99999999\n", encoding="utf-8")
                (lock_dir / "active.pid").write_text(f"{orphan.pid}\n", encoding="utf-8")
                (lock_dir / "shutdown_timeout").write_text("3\n", encoding="utf-8")
                project_lock.parent.mkdir(parents=True, exist_ok=True)
                project_lock.write_text(f"99999999\n{runtime_dir}\n", encoding="utf-8")

                blocked = self.run_script("check", "--frontend-only", "--install", "never")
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("live orphan active process group", blocked.stdout)
                self.assertTrue(project_lock.exists())
                self.assertTrue((lock_dir / "active.pid").exists())

                os.killpg(orphan.pid, signal.SIGTERM)
                orphan.wait(timeout=5)
                stop_env = self.isolated_env()
                stop_env["COGNITRADE_RUNTIME_DIR"] = str(runtime_dir)
                recovered = self.run_script("stop", env=stop_env)
                self.assertEqual(recovered.returncode, 0, recovered.stdout)
                self.assertFalse(project_lock.exists())
                self.assertFalse(lock_dir.exists())
            finally:
                if orphan is not None and orphan.poll() is None:
                    try:
                        os.killpg(orphan.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    orphan.wait(timeout=5)
                if project_lock.exists():
                    lines = project_lock.read_text(encoding="utf-8").splitlines()
                    if len(lines) > 1 and lines[1] == str(runtime_dir):
                        project_lock.unlink()

    def test_detached_full_service_reaches_ready_and_stops_cleanly(self) -> None:
        if shutil.which("node") is None or shutil.which("npm") is None:
            self.skipTest("frontend runtime is not installed")
        version_check = subprocess.run(
            [
                "node",
                "-e",
                "const [a,b]=process.versions.node.split('.').map(Number);"
                "process.exit(((a===20&&b>=19)||(a===22&&b>=12)||a>22)?0:1)",
            ],
            check=False,
        )
        if version_check.returncode != 0:
            self.skipTest("installed Node.js does not satisfy Vite")
        vite = REPO_ROOT / "frontend" / "node_modules" / ".bin" / "vite"
        if not vite.exists():
            self.skipTest("frontend dependencies are not installed")

        backend_port = self.free_port()
        frontend_port = self.free_port()
        while frontend_port == backend_port:
            frontend_port = self.free_port()
        with tempfile.TemporaryDirectory() as runtime_dir:
            env = self.isolated_env()
            stop_env = env.copy()
            stop_env["COGNITRADE_RUNTIME_DIR"] = runtime_dir
            started = False
            try:
                result = self.run_script(
                    "start",
                    "--detach",
                    "--install",
                    "never",
                    "--local-unauthenticated",
                    "--backend-port",
                    str(backend_port),
                    "--frontend-port",
                    str(frontend_port),
                    "--runtime-dir",
                    runtime_dir,
                    "--startup-timeout",
                    "60",
                    "--ready-timeout",
                    "30",
                    "--shutdown-timeout",
                    "5",
                    env=env,
                    timeout=75,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("detached service is ready", result.stdout)
                started = True

                status = self.run_script("status", env=stop_env)
                self.assertEqual(status.returncode, 0, status.stdout)
                self.assertIn("scope=all", status.stdout)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for url in (
                    f"http://127.0.0.1:{backend_port}/openapi.json",
                    f"http://127.0.0.1:{backend_port}/api/v1/system/status",
                    f"http://127.0.0.1:{frontend_port}/",
                ):
                    with opener.open(url, timeout=5) as response:
                        self.assertEqual(response.status, 200)
            finally:
                stopped = self.run_script("stop", env=stop_env, timeout=15)
                if started:
                    self.assertEqual(stopped.returncode, 0, stopped.stdout)

            self.assertFalse((Path(runtime_dir) / "lock").exists())
            for port in (backend_port, frontend_port):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1)
                    self.assertNotEqual(sock.connect_ex(("127.0.0.1", port)), 0)


if __name__ == "__main__":
    unittest.main()
