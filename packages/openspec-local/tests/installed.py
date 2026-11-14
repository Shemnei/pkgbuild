"""Exercise the installed CLI and distro completion files as an ordinary user."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="openspec-installed-") as temporary:
        home = Path(temporary)
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "NO_COLOR": "1",
            "OPENSPEC_TELEMETRY": "1",
            "DO_NOT_TRACK": "0",
            "CI": "false",
            "TMPDIR": os.environ.get("TMPDIR", temporary),
        }

        def run(*args, status=0):
            result = subprocess.run(
                args, cwd=home, env=env, capture_output=True, text=True, timeout=60
            )
            assert result.returncode == status, result.stdout + result.stderr
            return result.stdout

        assert run("openspec", "--version").strip() == "2.0.0"
        report = json.loads(run("openspec", "version", "--check", "--json"))
        assert report["install"]["packageManager"] == "pacman"
        assert report["update"]["status"] == "disabled"
        assert not report["update"]["canSelfUpgrade"]
        run("openspec", "install", status=1)
        run("openspec", "init", "--tools", "codex", "--no-animation")
        assert (home / ".agents/skills/openspec-propose/SKILL.md").is_file()
        run("openspec", "new", "change", "package-smoke")
        report = json.loads(run("openspec", "status", "--change", "package-smoke", "--json"))
        assert report["changeName"] == "package-smoke"
        json.loads(run("openspec", "update", "--dry-run", "--json"))
        run("openspec", "update", "--force")
        assert "package-smoke" in run("openspec", "__complete", "changes")
        cloud = home / "cloud"
        run(
            "openspec",
            "init",
            str(cloud),
            "--tools",
            "github-copilot",
            "--copilot-cloud",
            "--no-animation",
        )
        workflow = (cloud / ".github/workflows/copilot-setup-steps.yml").read_text()
        agent = (cloud / ".github/agents/openspec.agent.md").read_text()
        assert "command -v openspec" in workflow
        assert "npm install" not in workflow + agent
        assert "pacman" in agent
        bash = "/usr/share/bash-completion/completions/openspec"
        zsh = "/usr/share/zsh/site-functions/_openspec"
        fish = "/usr/share/fish/vendor_completions.d/openspec.fish"
        for shell, file in [("bash", bash), ("zsh", zsh), ("fish", fish)]:
            assert Path(file).stat().st_size > 0
            run(shell, "-n", file)
        output = run(
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            f"source {bash}; COMP_WORDS=(openspec va); COMP_CWORD=1; "
            '_openspec_completion; printf "%s\\n" "${COMPREPLY[@]}"',
        )
        assert "validate" in output.splitlines()
        run("zsh", "-f", "-c", f"autoload -Uz compinit; compinit -D; source {zsh}")
        output = run("fish", "--no-config", "-c", f"source {fish}; complete -C 'openspec va'")
        assert "validate" in output
        for file in home.rglob("*.json"):
            assert "anonymousId" not in file.read_text(), file
        assert Path("/usr/share/licenses/openspec-local/LICENSE").is_file()
        doc = Path("/usr/share/doc/openspec-local/README.md")
        assert f"openspec-local {doc}" in run("pacman", "-Ql", "openspec-local").splitlines()
        if doc.is_file():
            assert "OpenSpec" in doc.read_text()
        else:
            # The pinned official Arch image deliberately excludes documentation.
            assert "usr/share/doc/*" in run("pacman-conf", "NoExtract").splitlines()


if __name__ == "__main__":
    main()
