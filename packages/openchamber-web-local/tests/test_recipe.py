import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

RECIPE = Path(__file__).resolve().parents[1]


def load_module(name, directory=RECIPE):
    spec = importlib.util.spec_from_file_location(name, directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecipeTests(unittest.TestCase):
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

    def test_dependency_resolution_is_frozen_and_desktop_is_not_packaged(self):
        text = (RECIPE / "PKGBUILD").read_text()
        self.assertIn("--frozen-lockfile --ignore-scripts", text)
        self.assertNotIn("electron", RECIPE.name)

    def test_policy_refuses_unknown_sources_without_partial_edits(self):
        policy = load_module("prepare")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / next(iter(policy.EXPECTED))
            target.parent.mkdir(parents=True)
            target.write_text("unreviewed source")
            with self.assertRaisesRegex(ValueError, "Unreviewed"):
                policy.prepare(root)
            self.assertEqual(target.read_text(), "unreviewed source")
            # Even an earlier, checksum-valid input must remain untouched when
            # validation of a later input fails.
            policy.EXPECTED = {
                str(target.relative_to(root)): hashlib.sha256(target.read_bytes()).hexdigest(),
                "later.js": "0" * 64,
            }
            (root / "later.js").write_text("changed upstream")
            with self.assertRaisesRegex(ValueError, "Unreviewed"):
                policy.prepare(root)
            self.assertEqual(target.read_text(), "unreviewed source")

    def test_policy_boundaries_must_be_unique(self):
        policy = load_module("prepare")
        with self.assertRaises(ValueError):
            policy.replace_once("target target", "target", "replacement")
        with self.assertRaises(ValueError):
            policy.between("start only", "start", "end")

    def test_launcher_enforces_preferences_and_preserves_arguments(self):
        text = (RECIPE / "openchamber").read_text()
        for setting in (
            "export DO_NOT_TRACK=1",
            "export BUN_CONFIG_NO_TELEMETRY=1",
            "export OPENCODE_DISABLE_AUTOUPDATE=1",
        ):
            self.assertIn(setting, text)
        self.assertIn('exec /usr/bin/node /usr/lib/openchamber-web/bin/cli.js "$@"', text)

    def test_lint_baseline_rejects_new_findings_and_stale_exceptions(self):
        lint = load_module("lint")
        lint.validate([], set())
        with self.assertRaises(ValueError):
            lint.validate([], lint.BASELINE)
        diagnostic = {
            "filename": "src/new-file.ts",
            "code": "react-hooks(exhaustive-deps)",
            "severity": "warning",
            "message": "New finding",
            "labels": [{"span": {"line": 1, "column": 1}}],
        }
        with self.assertRaises(ValueError):
            lint.validate([diagnostic], set())
        key = lint.diagnostic_key(diagnostic)
        lint.validate([diagnostic], {key})
        with self.assertRaises(ValueError):
            lint.validate([diagnostic, diagnostic], {key})

    def test_failed_lint_retains_diagnostics_without_accepting_the_failure(self):
        lint = load_module("lint")
        error = subprocess.CalledProcessError(
            1, "lint:web", output="unexpected diagnostics", stderr="lint command failed"
        )
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "packages/web/src/index.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export {}")
            with patch.object(lint.subprocess, "run", side_effect=error), redirect_stderr(output):
                with self.assertRaises(subprocess.CalledProcessError):
                    lint.lint(root)
        self.assertIn("unexpected diagnostics", output.getvalue())
        self.assertIn("lint command failed", output.getvalue())

    def test_lint_passes_explicit_files_directly_and_rejects_incomplete_scans(self):
        lint = load_module("lint")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "packages/web/src/index.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export {}")
            (source.parent / "readme.txt").write_text("not a JavaScript source")
            for count in (0, 2):
                result = subprocess.CompletedProcess(
                    [], 0, json.dumps({"diagnostics": [], "number_of_files": count}), ""
                )
                with patch.object(lint.subprocess, "run", return_value=result) as run:
                    with self.assertRaisesRegex(ValueError, "Incomplete upstream web lint scan"):
                        lint.lint(root)
                self.assertEqual(run.call_args.args[0][0], str(root / "node_modules/.bin/oxlint"))
                self.assertEqual(run.call_args.args[0][-1], "src/index.ts")
                self.assertNotIn("src", run.call_args.args[0])
                self.assertEqual(run.call_args.kwargs["cwd"], root / "packages/web")

    def test_missing_lint_sources_fail_before_running_a_tool(self):
        lint = load_module("lint")
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(lint.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "No upstream web sources found"):
                    lint.lint(Path(temporary))
        run.assert_not_called()

    def test_upstream_completions_are_built_and_downstream_manuals_are_not_shipped(self):
        text = (RECIPE / "PKGBUILD").read_text()
        build, package = text.split("package()", 1)
        self.assertNotIn("help2man", text)
        self.assertNotIn("manual.h2m", text)
        self.assertFalse((RECIPE / "manual.h2m").exists())
        self.assertNotIn("/usr/share/man/", package)
        self.assertIn("$pkgdir/usr/share/doc/$pkgname/README.md", package)
        self.assertIn('tunnel completion "$_shell"', build)
        self.assertNotIn("tunnel completion", package)
        for location in (
            "bash-completion/completions/openchamber",
            "zsh/site-functions/_openchamber",
            "fish/vendor_completions.d/openchamber.fish",
        ):
            self.assertIn(location, package)

    def test_optional_integration_tools_are_only_required_for_installed_validation(self):
        text = (RECIPE / "PKGBUILD").read_text()
        self.assertNotIn("checkdepends=", text)
        manifest = tomllib.loads((RECIPE / "validation.toml").read_text())
        self.assertEqual(set(manifest["test_dependencies"]), {"systemd", "zsh", "fish"})
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
bun() { :; }
node() { :; }
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


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.web = self.root / "packages" / "web"
        self.web.mkdir(parents=True)
        for directory in ("bin", "server", "dist", "public"):
            (self.web / directory).mkdir()
        (self.web / "README.md").write_text("web")
        self.deployer = load_module("deploy")

    def package(self, directory, name, **fields):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text(json.dumps({"name": name, **fields}))
        (directory / "index.js").write_text(f"module.exports = {json.dumps(name)};")
        (directory / "LICENSE").write_text("test license")
        return directory

    def test_scoped_dependencies_cycles_versions_optional_and_peers(self):
        self.package(
            self.web,
            "web",
            dependencies={"@scope/first": "1", "second": "1"},
            optionalDependencies={"absent-platform": "1"},
            devDependencies={"development-only": "1"},
        )
        first = self.package(
            self.root / "node_modules/@scope/first",
            "@scope/first",
            dependencies={"second": "2"},
            peerDependencies={"peer": "1", "optional-peer": "1"},
            peerDependenciesMeta={"optional-peer": {"optional": True}},
        )
        self.package(first / "node_modules/second", "second-2", dependencies={"@scope/first": "1"})
        self.package(self.root / "node_modules/second", "second-1")
        self.package(self.root / "node_modules/peer", "peer")
        self.package(self.root / "node_modules/development-only", "development-only")
        (first / "private.test.js").write_text("not shipped")
        destination = self.root / "staged"
        self.deployer.deploy(self.web, destination)
        installed_first = (destination / "node_modules/@scope/first").resolve()
        nested = installed_first / "node_modules/second"
        self.assertEqual(json.loads((nested / "package.json").read_text())["name"], "second-2")
        self.assertEqual(
            json.loads((destination / "node_modules/second/package.json").read_text())["name"],
            "second-1",
        )
        self.assertTrue((installed_first / "node_modules/peer/package.json").is_file())
        self.assertFalse((destination / "node_modules/development-only").exists())
        self.assertFalse((installed_first / "private.test.js").exists())
        self.assertTrue((installed_first / "LICENSE").is_file())
        for file in destination.rglob("*"):
            if file.is_symlink():
                self.assertFalse(file.readlink().is_absolute())
                self.assertTrue(file.resolve(strict=True).is_relative_to(destination))

    def test_missing_required_dependencies_fail(self):
        self.package(self.web, "web", dependencies={"missing": "1"})
        with self.assertRaisesRegex(ValueError, "Missing production dependency"):
            self.deployer.deploy(self.web, self.root / "staged")

    def test_windows_only_web_dependency_is_not_deployed(self):
        self.package(self.web, "web", dependencies={"7zip-bin": "5.2.0"})
        self.package(self.root / "node_modules/7zip-bin", "7zip-bin")
        destination = self.root / "staged"
        self.deployer.deploy(self.web, destination)
        self.assertFalse((destination / "node_modules/7zip-bin").exists())

    def test_existing_destination_is_never_overwritten(self):
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            self.deployer.deploy(self.web, self.web)

    def test_invalid_dependency_name_is_rejected(self):
        for name in ("../escape", "/absolute", "a/../../b"):
            with self.assertRaises(ValueError):
                self.deployer.resolve_package(self.web, name)

    def test_known_native_packages_omit_foreign_binaries_and_build_tools(self):
        package = self.package(self.root / "native", "node-pty")
        for name in (
            "prebuilds/linux-arm64/pty.node",
            "third_party/conpty/conpty.dll",
            "build/node_gyp_bins/python3",
            "build/Release/obj.target/pty.o",
            "build/Release/pty.node",
        ):
            file = package / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture")
        target = self.root / "copied"
        self.deployer.copy_package(package, target)
        self.assertTrue((target / "build/Release/pty.node").is_file())
        for name in ("prebuilds", "third_party", "build/node_gyp_bins", "build/Release/obj.target"):
            self.assertFalse((target / name).exists())

        package = self.package(self.root / "bun-pty", "bun-pty")
        files = (
            "rust-pty/target/release/librust_pty.so",
            "rust-pty/target/release/librust_pty_arm64.so",
        )
        for entry in files:
            file = package / entry
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture")
        target = self.root / "copied-bun-pty"
        self.deployer.copy_package(package, target)
        self.assertTrue((target / files[0]).is_file())
        self.assertFalse((target / files[1]).exists())


if __name__ == "__main__":
    unittest.main()
