"""Validate the pacman-installed executable, not an upstream build tree."""

import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

from tui_smoke import smoke as tui_smoke

RECIPE = Path(__file__).resolve().parents[1]
if os.getuid() == 0:
    raise ValueError("Installed-package tests must not run as root")
spec = importlib.util.spec_from_file_location("installed_smoke", RECIPE / "smoke.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.smoke(Path("/usr/bin/opencode"))
tui_smoke(
    Path("/usr/bin/opencode"),
    Path(os.environ.get("PKGDEST", ".")) / "tui-smoke.log",
)

with tempfile.TemporaryDirectory(prefix="opencode-integration-") as home:
    environment = {"PATH": "/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8"}
    commands = (
        (
            "bash",
            "/usr/share/bash-completion/completions/opencode",
            "source /usr/share/bash-completion/completions/opencode; "
            'COMP_WORDS=(opencode s); COMP_CWORD=1; COMP_LINE="opencode s"; '
            '_opencode; printf "%s\\n" "${COMPREPLY[@]}"',
        ),
        (
            "zsh",
            "/usr/share/zsh/site-functions/_opencode",
            "autoload -Uz compinit; compinit -D; "
            "[[ ${_comps[opencode]} == _opencode ]] && print serve",
        ),
        (
            "fish",
            "/usr/share/fish/vendor_completions.d/opencode.fish",
            'complete --do-complete="opencode s"',
        ),
    )
    for shell, completion, command in commands:
        subprocess.run(
            [shell, "-n", completion],
            env=environment,
            cwd=home,
            check=True,
            timeout=30,
        )
        result = subprocess.run(
            [shell, "-c", command],
            env=environment,
            cwd=home,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        if "serve" not in result.stdout:
            raise ValueError(f"{shell} completion did not load: {result}")
    if not Path("/usr/share/doc/opencode-local/README.md").is_file():
        raise ValueError("Missing installed documentation")
