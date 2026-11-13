"""Exercise the built CLI without touching the current user's state or services."""

import argparse
import base64
import json
import selectors
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


def server_smoke(binary, home, env):
    with tempfile.TemporaryFile(mode="w+") as errors:
        process = subprocess.Popen(
            [binary, "serve", "--hostname", "127.0.0.1", "--port", "0", "--stdio"],
            cwd=home,
            env={
                **env,
                "OPENCODE_PASSWORD": "local-smoke-test",
                "OPENCODE_DISABLE_MODELS_FETCH": "1",
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 30
                address = None
                while time.monotonic() < deadline:
                    if not selector.select(timeout=max(0, deadline - time.monotonic())):
                        break
                    line = process.stdout.readline()
                    if not line:
                        break
                    if line.startswith(b'{"url":'):
                        address = json.loads(line)["url"]
                        break
            if address is None:
                errors.seek(0)
                raise ValueError(f"Server did not become ready: {errors.read()}")
            # Never follow a server-supplied URL outside the requested loopback address.
            if not address.startswith("http://127.0.0.1:"):
                raise ValueError(f"Unexpected listen address: {address}")
            auth = base64.b64encode(b"opencode:local-smoke-test").decode()
            request = Request(
                f"{address.rstrip('/')}/api/info", headers={"Authorization": f"Basic {auth}"}
            )
            with urlopen(request, timeout=10) as response:
                info = json.load(response)
            if info["version"] != "2.2.7":
                raise ValueError(f"Unexpected server identity: {info}")
        finally:
            # stdio mode owns this process only; EOF shuts down the temporary server.
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


def smoke(binary):
    binary = binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="opencode-smoke-") as home:
        temporary = Path(home) / "tmp"
        temporary.mkdir()
        installed = Path(home) / "opencode"
        shutil.copyfile(binary, installed)
        installed.chmod(0o755)
        binary = installed
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": home,
            "TMPDIR": str(temporary),
            "XDG_CONFIG_HOME": f"{home}/config",
            "XDG_DATA_HOME": f"{home}/data",
            "XDG_STATE_HOME": f"{home}/state",
            "XDG_CACHE_HOME": f"{home}/cache",
            "DO_NOT_TRACK": "1",
            "BUN_CONFIG_NO_TELEMETRY": "1",
            # Deliberately hostile reporting endpoint and updater preference:
            # implementation policy, not opt-out environment, must win.
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1",
            "OPENCODE_DISABLE_AUTOUPDATE": "false",
        }
        config = Path(home) / "config" / "opencode"
        config.mkdir(parents=True)
        (config / "opencode.json").write_text('{"update":"auto"}\n')
        sentinel = config / "keep-user-data"
        sentinel.write_text("untouched")
        for args, marker in ((["--version"], "2.2.7"), (["--help"], "opencode")):
            result = subprocess.run(
                [binary, *args],
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode:
                raise ValueError(
                    f"CLI command failed ({result.returncode}): {args}: "
                    f"{result.stdout}{result.stderr}"
                )
            if marker not in result.stdout:
                raise ValueError(f"Missing {marker!r} from {args}: {result.stdout}")
        for args in (
            ["upgrade"],
            ["update"],
            ["upgrade", "99.0.0", "--method", "curl"],
            ["uninstall", "--force"],
        ):
            result = subprocess.run(
                [binary, *args],
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0 or "pacman" not in result.stdout + result.stderr:
                raise ValueError(f"Command did not refuse safely: {args}: {result}")
        if sentinel.read_text() != "untouched":
            raise ValueError("Self-uninstall modified user configuration")
        server_smoke(binary, home, env)
    print("Built CLI, pacman-only update/removal, and loopback API smoke tests passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    smoke(parser.parse_args().binary)
