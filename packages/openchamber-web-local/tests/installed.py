"""Installed launcher, production dependency closure, and native-terminal checks."""

import os
import subprocess
import tempfile
from pathlib import Path

from http_smoke import smoke

if os.getuid() == 0:
    raise ValueError("Installed-package tests must not run as root")

with tempfile.TemporaryDirectory(prefix="openchamber-installed-") as temporary:
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": temporary,
        "XDG_CONFIG_HOME": f"{temporary}/config",
        "XDG_DATA_HOME": f"{temporary}/data",
        "XDG_STATE_HOME": f"{temporary}/state",
        "XDG_CACHE_HOME": f"{temporary}/cache",
        "OPENCHAMBER_DATA_DIR": f"{temporary}/openchamber",
        "DO_NOT_TRACK": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCHAMBER_STAGED": "/usr/lib/openchamber-web",
        # The existing staged checks need a writable temporary location.
        "TMPDIR": temporary,
        "XDG_RUNTIME_DIR": f"{temporary}/runtime",
    }
    for arguments, marker in ((["--version"], "2.7.0"), (["--help"], "usage")):
        result = subprocess.run(
            ["/usr/bin/openchamber", *arguments],
            cwd=temporary,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        if marker not in result.stdout.lower():
            raise ValueError(f"Unexpected launcher output: {result.stdout}")
    result = subprocess.run(
        ["/usr/bin/openchamber", "update"],
        cwd=temporary,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode == 0 or "pacman" not in result.stdout + result.stderr:
        raise ValueError("Installed self-update handler did not refuse with pacman guidance")
    subprocess.run(
        ["/usr/bin/node", "--test", str(Path(__file__).resolve().parents[1] / "staged.test.mjs")],
        cwd=temporary,
        env=environment,
        check=True,
        timeout=120,
    )
    Path(environment["XDG_RUNTIME_DIR"]).mkdir(mode=0o700)
    subprocess.run(
        ["systemd-analyze", "--user", "--man=no", "verify", "openchamber-web.service"],
        env=environment,
        cwd=temporary,
        check=True,
        timeout=30,
    )
    if not Path("/usr/share/doc/openchamber-web-local/README.md").is_file():
        raise ValueError("Missing installed documentation")
    for shell, completion, command in (
        (
            "bash",
            "/usr/share/bash-completion/completions/openchamber",
            "source /usr/share/bash-completion/completions/openchamber; "
            "COMP_WORDS=(openchamber tunnel sta); COMP_CWORD=2; "
            '_openchamber_tunnel; printf "%s\\n" "${COMPREPLY[@]}"',
        ),
        (
            "zsh",
            "/usr/share/zsh/site-functions/_openchamber",
            "autoload -Uz compinit; compinit -D; "
            "[[ ${_comps[openchamber]} == _openchamber ]] && print start",
        ),
        (
            "fish",
            "/usr/share/fish/vendor_completions.d/openchamber.fish",
            'complete --do-complete="openchamber tunnel sta"',
        ),
    ):
        subprocess.run(
            [shell, "-n", completion],
            env=environment,
            cwd=temporary,
            check=True,
            timeout=30,
        )
        result = subprocess.run(
            [shell, "-c", command],
            cwd=temporary,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        if "start" not in result.stdout:
            raise ValueError(f"{shell} completion did not load: {result}")
    smoke(environment, temporary)
