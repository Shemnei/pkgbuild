"""Exercise the packaged HTTP boundary with an isolated home and owned processes."""

import json
import os
import selectors
import signal
import socket
import subprocess
import tempfile
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def smoke(environment, home, launcher=("/usr/bin/openchamber",)):
    data = Path(environment["OPENCHAMBER_DATA_DIR"])
    data.mkdir(parents=True, exist_ok=True)
    # An old telemetry opt-in must not restore the removed reporting boundary.
    (data / "settings.json").write_text('{"reportUsage":true}\n')
    # The CLI rejects --port 0, unlike the underlying server API. Select a free
    # loopback port; a bind race remains a real startup failure, never a skip.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        requested_port = reservation.getsockname()[1]
    with tempfile.TemporaryFile(mode="w+") as errors:
        process = subprocess.Popen(
            [
                *launcher,
                "serve",
                "--foreground",
                "--quiet",
                "--host",
                "127.0.0.1",
                "--port",
                str(requested_port),
                "--ui-password",
                "installed-test-password",
            ],
            env={**environment, "OPENCHAMBER_RELAY_HOST": "off"},
            cwd=home,
            stdout=subprocess.PIPE,
            stderr=errors,
            start_new_session=True,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 90
                port = None
                while time.monotonic() < deadline:
                    if not selector.select(timeout=max(0, deadline - time.monotonic())):
                        break
                    line = process.stdout.readline()
                    if not line:
                        break
                    if line.strip().isdigit():
                        port = int(line.strip())
                        break
            if port is None or not 0 < port < 65536:
                errors.seek(0)
                logs = "\n".join(path.read_text() for path in data.rglob("*.log"))
                raise ValueError(f"Installed server did not become ready: {errors.read()}\n{logs}")
            origin = f"http://127.0.0.1:{port}"
            client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

            def request(path, body=None):
                req = Request(
                    origin + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={"Origin": origin, "Content-Type": "application/json"},
                )
                try:
                    response = client.open(req, timeout=30)
                except HTTPError as error:
                    response = error
                with response:
                    content = response.read().decode()
                    if "application/json" in response.headers.get("Content-Type", ""):
                        content = json.loads(content)
                    return response.status, content

            status, _ = request("/api/config/settings")
            if status != 401:
                raise ValueError(f"Unauthenticated API was not rejected: {status}")
            status, body = request("/auth/session", {"password": "installed-test-password"})
            if status != 200:
                raise ValueError(f"Password authentication failed: {status}: {body}")
            status, body = request("/")
            if status != 200 or "<script" not in body:
                raise ValueError(f"Production UI was not served: {status}")
            status, body = request("/api/config/settings")
            if status != 200 or body.get("reportUsage") is not False:
                raise ValueError(f"Saved reporting opt-in was not overridden: {status}: {body}")
            for path in (
                "/api/openchamber/update-check?reportUsage=true&installId=old-tracking-id",
                "/api/opencode/upgrade-status",
            ):
                status, body = request(path)
                if status != 200 or body.get("available") is not False:
                    raise ValueError(f"Unexpected update status: {path}: {status}: {body}")
            for path in (
                "/api/openchamber/update-install",
                "/api/opencode/upgrade",
                "/api/opencode/install-v2",
            ):
                status, body = request(path, {})
                if status != 409 or body.get("code") != "PACMAN_MANAGED":
                    raise ValueError(f"Update was not refused: {path}: {status}: {body}")
            result = subprocess.run(
                [*launcher, "update"],
                env=environment,
                cwd=home,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0 or "pacman" not in result.stdout + result.stderr:
                raise ValueError(f"Running-instance CLI update was not refused: {result}")
            status, _ = request("/api/config/settings")
            if status != 200 or process.poll() is not None:
                raise ValueError("Refused update stopped the running server")
        finally:
            # Own the whole test process group, including any spawned OpenCode server.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except ProcessLookupError:
                process.wait(timeout=5)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.stdout.close()
    print("HTTP authentication, UI assets, saved opt-in, and updater refusals passed")
