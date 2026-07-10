from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"
COMPOSE_FILE = REPO_ROOT / "compose.yaml"


class DeployScriptTests(unittest.TestCase):
    def run_script(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DEPLOY_SCRIPT), *args],
            cwd=cwd or REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )

    def test_script_is_executable_and_has_valid_bash_syntax(self) -> None:
        self.assertTrue(os.access(DEPLOY_SCRIPT, os.X_OK))
        result = subprocess.run(
            ["bash", "-n", str(DEPLOY_SCRIPT)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_help_works_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_script("--help", cwd=Path(temporary))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("start API + frontend web", result.stdout)
        self.assertIn("Start both from existing images", result.stdout)
        self.assertIn("Debian/Ubuntu", result.stdout)

    def test_compose_starts_frontend_web_after_healthy_api(self) -> None:
        payload = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
        services = payload["services"]

        self.assertEqual(set(services), {"api", "web"})
        self.assertEqual(services["web"]["depends_on"]["api"]["condition"], "service_healthy")
        self.assertEqual(services["web"]["ports"], ["${COGNITRADE_WEB_HOST:-127.0.0.1}:${COGNITRADE_WEB_PORT:-8080}:8080"])
        self.assertEqual(services["web"]["build"]["args"]["VITE_COGNITRADE_API_BASE_URL"], "/api/v1")

    def test_init_generates_secure_token_and_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "deploy.env"
            result = self.run_script(
                "init",
                "--env-file",
                str(env_file),
                "--web-port",
                "18080",
            )
            payload = env_file.read_text(encoding="utf-8")
            mode = env_file.stat().st_mode & 0o777

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(mode, 0o600)
        values = self._env_values(payload)
        self.assertEqual(len(values["COGNITRADE_API_TOKEN"]), 64)
        int(values["COGNITRADE_API_TOKEN"], 16)
        self.assertEqual(values["COGNITRADE_API_ALLOW_UNAUTHENTICATED"], "false")
        self.assertEqual(values["COGNITRADE_WEB_PORT"], "18080")
        self.assertEqual(values["COGNITRADE_WORKSPACE_VOLUME"], "cognitrade_workspace")

    def test_init_preserves_existing_token(self) -> None:
        token = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "deploy.env"
            env_file.write_text(
                "\n".join(
                    (
                        f"COGNITRADE_API_TOKEN={token}",
                        "COGNITRADE_API_ALLOW_UNAUTHENTICATED=true",
                        "COGNITRADE_WEB_HOST=127.0.0.1",
                        "COGNITRADE_WEB_PORT=8080",
                        "OPENAI_API_KEY=",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run_script("init", "--env-file", str(env_file))
            values = self._env_values(env_file.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(values["COGNITRADE_API_TOKEN"], token)
        self.assertEqual(values["COGNITRADE_API_ALLOW_UNAUTHENTICATED"], "false")

    def test_invalid_web_port_is_rejected(self) -> None:
        result = self.run_script("init", "--web-port", "70000")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("between 1 and 65535", result.stdout)

    def test_check_accepts_a_valid_compose_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            env_file = temporary_path / "deploy.env"
            initialized = self.run_script("init", "--env-file", str(env_file))
            self.assertEqual(initialized.returncode, 0, initialized.stdout)

            fake_docker = temporary_path / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  info) exit 0 ;;\n"
                "  version) printf '99.0.0\\n'; exit 0 ;;\n"
                "  compose)\n"
                "    if [ \"${2:-}\" = version ]; then printf '99.0.0\\n'; fi\n"
                "    exit 0 ;;\n"
                "esac\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            environment = os.environ.copy()
            environment["COGNITRADE_DOCKER_BIN"] = str(fake_docker)
            result = self.run_script(
                "check",
                "--env-file",
                str(env_file),
                env=environment,
            )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("deployment preflight passed", result.stdout)

    def test_dry_run_does_not_create_environment_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "deploy.env"
            environment = os.environ.copy()
            environment["COGNITRADE_DOCKER_BIN"] = str(Path(temporary) / "missing-docker")
            result = self.run_script(
                "deploy",
                "--dry-run",
                "--yes",
                "--env-file",
                str(env_file),
                env=environment,
            )
            exists = env_file.exists()

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(exists)
        self.assertIn("would install Docker", result.stdout)
        self.assertIn("compose", result.stdout)
        self.assertIn("build", result.stdout)
        self.assertIn("starting API and frontend web containers", result.stdout)

    @staticmethod
    def _env_values(payload: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
        return values


if __name__ == "__main__":
    unittest.main()
