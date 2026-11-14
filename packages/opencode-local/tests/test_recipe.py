import hashlib
import importlib.util
import io
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

RECIPE = Path(__file__).resolve().parents[1]


def recipe_module(name):
    spec = importlib.util.spec_from_file_location(f"opencode_{name}", RECIPE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenCodeTests(unittest.TestCase):
    def test_build_phases_prefer_system_tools_in_parent_and_child_shells(self):
        with tempfile.TemporaryDirectory() as temporary:
            shadow = Path(temporary) / "bash"
            shadow.write_text("#!/bin/sh\nexit 99\n")
            shadow.chmod(0o755)
            for phase in ("prepare", "build", "check"):
                # Stop at cd so no build, dependency fetch, or policy script runs.
                result = subprocess.run(
                    [
                        "/usr/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-c",
                        """
set -eu
source "$1"
srcdir=$2
export PATH="$2:/usr/bin"
hash -p "$2/bash" bash
cd() {
  command -v bash
  bash --noprofile --norc -c 'command -v bash; printf "%s\\n" "$PATH"'
  return 1
}
"$3"
""",
                        "recipe-test",
                        str(RECIPE / "PKGBUILD"),
                        temporary,
                        phase,
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                with self.subTest(phase=phase):
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(
                        result.stdout.splitlines(),
                        ["/usr/bin/bash", "/usr/bin/bash", f"/usr/bin:{temporary}:/usr/bin"],
                    )

    def test_drift_validation_is_atomic(self):
        module = recipe_module("prepare")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = b"first reviewed input"
            (root / "first").write_bytes(original)
            (root / "later").write_bytes(b"unreviewed input")
            module.EXPECTED = {
                "first": hashlib.sha256(original).hexdigest(),
                "later": "0" * 64,
            }
            with self.assertRaisesRegex(ValueError, "Unreviewed upstream source: later"):
                module.prepare(root)
            self.assertEqual((root / "first").read_bytes(), original)

    def test_boundaries_reject_missing_and_duplicate_matches(self):
        module = recipe_module("prepare")
        for text in ("absent", "target target"):
            with self.assertRaises(ValueError):
                module.replace_once(text, "target", "replacement")
        self.assertEqual(module.replace_once("a target b", "target", "new"), "a new b")

    def test_cli_build_disables_mutable_install_and_web_desktop_builds(self):
        text = (RECIPE / "PKGBUILD").read_text()
        self.assertIn("--frozen-lockfile --ignore-scripts", text)
        self.assertNotIn("electron", RECIPE.name)
        self.assertIn('provides=("opencode=$pkgver")', text)
        self.assertIn("--skip-install --skip-web-ui", text)
        self.assertIn("OPENCODE_TARGET=linux-x64", text)
        self.assertIn("unset BUN_COMPILE_RELEASE", text)
        self.assertIn("'bun>=1.4.4'", text)
        self.assertIn("depends=('glibc' 'gcc-libs' 'icu' 'ripgrep' 'git')", text)
        self.assertIn("'!strip'", text)
        self.assertNotIn("node_modules", text.split("package()", 1)[1])

    def test_build_uses_standard_release_channel_even_when_environment_selects_local(self):
        result = subprocess.run(
            [
                "/usr/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                """
set -e
source "$1"
srcdir=/unused
cd() { :; }
export OPENCODE_CHANNEL=local
bun() {
  printf '%s\\n' "$OPENCODE_CHANNEL" "$OPENCODE_VERSION"
  return 1
}
build
""",
                "release-channel-test",
                str(RECIPE / "PKGBUILD"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["latest", "2.2.7"])

    def test_smoke_commands_isolate_user_environment_and_require_refusals(self):
        module = recipe_module("smoke")
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            command = args[1]
            if command == "--version":
                return subprocess.CompletedProcess(args, 0, "2.2.7", "")
            if command == "--help":
                return subprocess.CompletedProcess(args, 0, "opencode", "")
            if args[1:] == ["debug", "paths", "db"]:
                return subprocess.CompletedProcess(
                    args, 0, f"{kwargs['env']['XDG_DATA_HOME']}/opencode/opencode.db\n", ""
                )
            return subprocess.CompletedProcess(args, 1, "", "Use pacman")

        with (
            patch.object(module.subprocess, "run", side_effect=run),
            patch.object(module, "server_smoke") as server,
            redirect_stdout(io.StringIO()),
        ):
            module.smoke(RECIPE / "PKGBUILD")
        self.assertEqual(len(calls), 7)
        server.assert_called_once()
        for _, kwargs in calls:
            self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
            self.assertNotIn("OPENCODE_DB", kwargs["env"])
            self.assertNotIn("OPENCODE_DISABLE_CHANNEL_DB", kwargs["env"])
            self.assertEqual(kwargs["env"]["OPENCODE_DISABLE_AUTOUPDATE"], "false")
            self.assertNotEqual(Path(kwargs["cwd"]), Path.home())
            self.assertNotEqual(kwargs["env"]["TMPDIR"], kwargs["env"]["HOME"])
            self.assertEqual(Path(kwargs["env"]["TMPDIR"]).parent, Path(kwargs["env"]["HOME"]))
        self.assertFalse(Path(calls[0][1]["cwd"]).exists())

    def test_smoke_rejects_a_separate_channel_database(self):
        module = recipe_module("smoke")
        results = [
            subprocess.CompletedProcess([], 0, "2.2.7", ""),
            subprocess.CompletedProcess([], 0, "opencode", ""),
            subprocess.CompletedProcess([], 0, "/data/opencode/opencode-local.db", ""),
        ]
        with patch.object(module.subprocess, "run", side_effect=results):
            with self.assertRaisesRegex(ValueError, "opencode.db"):
                module.smoke(RECIPE / "PKGBUILD")

    def test_upstream_completions_are_built_and_downstream_manuals_are_not_shipped(self):
        text = (RECIPE / "PKGBUILD").read_text()
        build, package = text.split("package()", 1)
        self.assertIn('--completions "$_shell"', build)
        self.assertNotIn("help2man", text)
        self.assertNotIn("manual.h2m", text)
        self.assertFalse((RECIPE / "manual.h2m").exists())
        self.assertNotIn("/usr/share/man/", package)
        self.assertNotIn("--completions", package)
        for path in (
            "bash-completion/completions/opencode",
            "zsh/site-functions/_opencode",
            "fish/vendor_completions.d/opencode.fish",
            "doc/$pkgname/README.md",
        ):
            self.assertIn(f"$pkgdir/usr/share/{path}", package)

    def test_optional_integration_tools_are_only_required_for_installed_validation(self):
        text = (RECIPE / "PKGBUILD").read_text()
        self.assertNotIn("checkdepends=", text)
        manifest = tomllib.loads((RECIPE / "validation.toml").read_text())
        self.assertEqual(set(manifest["test_dependencies"]), {"zsh", "fish"})
        result = subprocess.run(
            [
                "/usr/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                """
set -e
source "$1"
srcdir=/unused
cd() { :; }
python() { :; }
zsh() { return 127; }
fish() { return 127; }
check
""",
                "integration-dependencies-test",
                str(RECIPE / "PKGBUILD"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_smoke_rejects_a_successful_self_update(self):
        module = recipe_module("smoke")

        def run(args, **kwargs):
            output = {
                "--version": "2.2.7",
                "--help": "opencode",
                "debug": f"{kwargs['env']['XDG_DATA_HOME']}/opencode/opencode.db\n",
            }.get(args[1], "Updated successfully")
            return subprocess.CompletedProcess(args, 0, output, "")

        with patch.object(module.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "did not refuse safely"):
                module.smoke(RECIPE / "PKGBUILD")

    def test_smoke_retains_stderr_when_an_executable_cannot_start(self):
        module = recipe_module("smoke")
        failure = subprocess.CompletedProcess([], 1, "", "native module failed to load")
        with patch.object(module.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(ValueError, "native module failed to load"):
                module.smoke(RECIPE / "PKGBUILD")
