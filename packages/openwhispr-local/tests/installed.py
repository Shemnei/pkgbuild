"""Exercise the installed desktop application as an unprivileged user."""

import os
import signal
import socket
import subprocess
import tempfile
from pathlib import Path

if os.getuid() == 0:
    raise ValueError("Installed-package tests must not run as root")

RECIPE = Path(__file__).resolve().parents[1]
ROOT = Path("/usr/lib/openwhispr")
for name in (
    "main.js",
    "preload.js",
    "src/dist/index.html",
    "resources/bin/linux-fast-paste",
    "resources/bin/linux-key-listener-x64",
    "resources/bin/linux-text-monitor",
    "resources/bin/linux-system-audio-helper",
    "resources/bin/whisper-server",
):
    if not (ROOT / name).is_file():
        raise ValueError(f"Missing runtime file: {name}")
for file in ROOT.rglob("*"):
    if file.is_symlink():
        resolved = file.resolve(strict=True)
        if not resolved.is_relative_to(ROOT) and not resolved.is_relative_to("/usr"):
            raise ValueError(f"Runtime link escapes installed paths: {file}")
for dependency in (
    "electron",
    "electron-builder",
    "typescript",
    "vite",
    "eslint",
    "shadcn-ui",
    "electron-updater",
    "ffmpeg-static",
    "prebuild-install",
):
    if (ROOT / "node_modules" / dependency).exists():
        raise ValueError(f"Development-only dependency shipped: {dependency}")
for name in (
    "/usr/share/icons/hicolor/256x256/apps/open-whispr.png",
    "/usr/share/licenses/openwhispr-local/LICENSE",
    "/usr/share/licenses/openwhispr-local/onnxruntime-ThirdPartyNotices.txt",
    "/usr/share/licenses/openwhispr-local/whisper.cpp-LICENSE",
    "/usr/share/doc/openwhispr-local/README.md",
):
    if not Path(name).is_file():
        raise ValueError(f"Missing desktop integration or documentation: {name}")

with tempfile.TemporaryDirectory(prefix="openwhispr-installed-") as temporary:
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": temporary,
        "TMPDIR": temporary,
        "XDG_CONFIG_HOME": f"{temporary}/config",
        "XDG_DATA_HOME": f"{temporary}/data",
        "XDG_CACHE_HOME": f"{temporary}/cache",
        "XDG_STATE_HOME": f"{temporary}/state",
        "XDG_RUNTIME_DIR": f"{temporary}/runtime",
        "NODE_ENV": "production",
        "OPENWHISPR_CHANNEL": "production",
        "OPENWHISPR_SOURCE": str(ROOT),
        # Deliberately enable dependency reporting to test hard enforcement.
        "BETTER_AUTH_TELEMETRY": "1",
        "BETTER_AUTH_TELEMETRY_ENDPOINT": "https://example.invalid",
    }
    Path(environment["XDG_RUNTIME_DIR"]).mkdir(mode=0o700)
    subprocess.run(
        [str(ROOT / "resources/bin/whisper-server"), "--help"],
        env=environment,
        check=True,
        timeout=30,
        capture_output=True,
    )
    subprocess.run(
        ["desktop-file-validate", "/usr/share/applications/open-whispr.desktop"], check=True
    )
    for test in ("policy.test.mjs", "source-policy.test.mjs"):
        subprocess.run(
            ["node", "--test", str(RECIPE / test)], env=environment, check=True, timeout=60
        )
    subprocess.run(
        [
            "electron43",
            "-e",
            'const db = new (require("/usr/lib/openwhispr/node_modules/better-sqlite3"))(":memory:");'
            'if (db.prepare("select 1 as ok").get().ok !== 1) process.exit(1); db.close();'
            'require("/usr/lib/openwhispr/node_modules/onnxruntime-node");'
            'require("/usr/lib/openwhispr/node_modules/@napi-rs/keyring");',
        ],
        env={**environment, "ELECTRON_RUN_AS_NODE": "1"},
        check=True,
        timeout=30,
    )
    # The debugging socket exists only in this isolated validation process, on loopback.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with Path(temporary, "desktop.log").open("w+") as log:
        process = subprocess.Popen(
            [
                "xvfb-run",
                "-a",
                "dbus-run-session",
                "--",
                "/usr/bin/openwhispr",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={port}",
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            subprocess.run(
                ["node", str(RECIPE / "tests/gui.mjs"), str(port)],
                env=environment,
                check=True,
                timeout=100,
            )
            if process.poll() is not None:
                raise ValueError("Desktop application exited prematurely")
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            log.seek(0)
            print(log.read())
