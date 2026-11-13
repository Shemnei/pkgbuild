import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        spec = importlib.util.spec_from_file_location("repo", ROOT / "support" / "repo.py")
        self.repo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.repo)
        self.repo.ROOT = self.root
        for name in ("example-local", "other-local"):
            directory = self.root / "packages" / name
            directory.mkdir(parents=True)
            (directory / "PKGBUILD").write_text(f"pkgname={name}\n")
        self.recipe = self.root / "packages" / "example-local"

    def test_build_install_and_chroot_use_the_selected_recipe(self):
        commands = {
            "build": ("makepkg", "--cleanbuild", "--log"),
            "install": ("makepkg", "--cleanbuild", "--log", "--syncdeps", "--install"),
            "chroot": ("extra-x86_64-build",),
        }
        for directory in self.repo.recipes():
            for command, arguments in commands.items():
                with (
                    self.subTest(package=directory.name, command=command),
                    patch("sys.argv", ["repo.py", command, directory.name]),
                    patch.object(self.repo, "run") as run,
                ):
                    self.repo.main()
                    run.assert_called_once_with(*arguments, cwd=directory)

    def test_install_rejects_missing_or_unknown_packages_before_running_commands(self):
        for arguments in ([], ["unknown-local"], ["../example-local"], ["--all"]):
            with (
                self.subTest(arguments=arguments),
                patch("sys.argv", ["repo.py", "install", *arguments]),
                patch.object(self.repo, "run") as run,
                self.assertRaises((RuntimeError, SystemExit)),
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                self.repo.main()
            run.assert_not_called()

    def test_install_propagates_makepkg_failures_without_retrying(self):
        error = subprocess.CalledProcessError(1, "makepkg")
        with (
            patch("sys.argv", ["repo.py", "install", self.recipe.name]),
            patch.object(self.repo, "run", side_effect=error) as run,
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            self.repo.main()
        self.assertIs(raised.exception, error)
        run.assert_called_once()

    def test_just_install_dispatch_and_exit_status_with_a_fake_makepkg(self):
        (self.root / "support").mkdir()
        shutil.copyfile(ROOT / "support" / "repo.py", self.root / "support" / "repo.py")
        shutil.copyfile(ROOT / "justfile", self.root / "justfile")
        binaries = self.root / "bin"
        binaries.mkdir()
        makepkg = binaries / "makepkg"
        for status in (0, 17):
            makepkg.write_text(f'#!/bin/sh\npwd\nprintf "%s\\n" "$@"\nexit {status}\n')
            makepkg.chmod(0o755)
            result = subprocess.run(
                ["just", "install", self.recipe.name],
                cwd=self.root,
                env={**os.environ, "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}"},
                text=True,
                capture_output=True,
                check=False,
            )
            with self.subTest(status=status):
                self.assertEqual(result.returncode == 0, status == 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines()[1:],
                    [str(self.recipe), "--cleanbuild", "--log", "--syncdeps", "--install"],
                )

    def test_namcap_findings_fail_even_when_the_tool_exits_zero(self):
        for output in (
            "PKGBUILD (package) E: Invalid checksum\n",
            "package W: Unused dependency\n",
        ):
            with (
                patch.object(self.repo, "run", return_value=output),
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(RuntimeError, "namcap finding"):
                    self.repo.namcap("PKGBUILD", self.recipe)
        with patch.object(self.repo, "run", return_value=""), redirect_stdout(io.StringIO()):
            self.repo.namcap("PKGBUILD", self.recipe)

    def write_namcap_exceptions(self, entries, version="1.0-1"):
        path = self.recipe / "namcap.toml"
        path.write_text(
            f'pkgname = "example-local"\npkgver = {json.dumps(version)}\n'
            'build_packages = ["runtime-1.0-1"]\n'
            + "\n".join(
                "[[exceptions]]\n"
                + "\n".join(f"{key} = {json.dumps(value)}" for key, value in entry.items())
                for entry in entries
            )
        )
        return path

    def test_archive_lint_accepts_only_exact_versioned_documented_warnings(self):
        diagnostic = "example-local W: Known upstream limitation"
        self.write_namcap_exceptions([{"diagnostic": diagnostic, "reason": "Reviewed reason"}])
        metadata = "pkgname = example-local\npkgver = 1.0-1\ninstalled = runtime-1.0-1\n"
        output = io.StringIO()
        with (
            patch.object(self.repo, "run", side_effect=[diagnostic + "\n", metadata]) as run,
            redirect_stdout(output),
        ):
            self.repo.namcap("archive.pkg.tar.zst", self.recipe)
        self.assertIn("Reviewed reason", output.getvalue())
        self.assertEqual(
            run.call_args_list[-1].args,
            ("bsdtar", "-xOf", "archive.pkg.tar.zst", ".PKGINFO", ".BUILDINFO"),
        )
        for findings in (
            "",
            diagnostic + " changed\n",
            diagnostic + "\n" + diagnostic + "\n",
            diagnostic + "\nexample-local E: New error\n",
        ):
            with (
                self.subTest(findings=findings),
                patch.object(self.repo, "run", side_effect=[findings, metadata]),
                redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, "namcap finding or stale exception"),
            ):
                self.repo.namcap("archive.pkg.tar.zst", self.recipe)

    def test_archive_lint_rejects_wrong_versions_and_invalid_exceptions(self):
        diagnostic = "example-local W: Known upstream limitation"
        metadata = "pkgname = example-local\npkgver = 1.0-1\ninstalled = runtime-1.0-1\n"
        entries = [
            [{"diagnostic": diagnostic, "reason": ""}],
            [{"diagnostic": diagnostic, "reason": "Reviewed", "unknown": "field"}],
            [{"diagnostic": "example-local E: Error", "reason": "Cannot except errors"}],
            [{"diagnostic": diagnostic + "\nsecond line", "reason": "Not exact"}],
            [{"diagnostic": diagnostic, "reason": "Reviewed"}] * 2,
        ]
        for items in entries:
            self.write_namcap_exceptions(items)
            with (
                self.subTest(entries=items),
                patch.object(self.repo, "run", side_effect=[diagnostic, metadata]),
                redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, "Invalid or duplicate"),
            ):
                self.repo.namcap("archive.pkg.tar.zst", self.recipe)
        self.write_namcap_exceptions([{"diagnostic": diagnostic, "reason": "Reviewed"}], "2.0-1")
        with (
            patch.object(self.repo, "run", side_effect=[diagnostic, metadata]),
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "do not match package version"),
        ):
            self.repo.namcap("archive.pkg.tar.zst", self.recipe)

    def test_archive_lint_requires_the_audited_build_runtime(self):
        diagnostic = "example-local W: Known upstream limitation"
        self.write_namcap_exceptions([{"diagnostic": diagnostic, "reason": "Reviewed"}])
        metadata = "pkgname = example-local\npkgver = 1.0-1\ninstalled = runtime-2.0-1\n"
        with (
            patch.object(self.repo, "run", side_effect=[diagnostic, metadata]),
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "build-package pins do not match"),
        ):
            self.repo.namcap("archive.pkg.tar.zst", self.recipe)

    def test_archive_lint_rejects_empty_or_unknown_policy_and_tool_failures(self):
        for text in (
            'pkgname = "example-local"\nunknown = true\n',
            'pkgname = "example-local"\npkgver = "1.0-1"\nbuild_packages = []\nexceptions = []\n',
        ):
            (self.recipe / "namcap.toml").write_text(text)
            with (
                self.subTest(text=text),
                patch.object(
                    self.repo, "run", side_effect=["", "pkgname = example-local\npkgver = 1.0-1\n"]
                ),
                redirect_stdout(io.StringIO()),
                self.assertRaises(RuntimeError),
            ):
                self.repo.namcap("archive.pkg.tar.zst", self.recipe)
        with (
            patch.object(self.repo, "run", side_effect=subprocess.CalledProcessError(1, "namcap")),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.repo.namcap("archive.pkg.tar.zst", self.recipe)

    def test_source_recipe_lint_never_uses_archive_exceptions(self):
        diagnostic = "example-local W: Known upstream limitation"
        self.write_namcap_exceptions([{"diagnostic": diagnostic, "reason": "Reviewed"}])
        with (
            patch.object(self.repo, "run", return_value=diagnostic) as run,
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "namcap finding"),
        ):
            self.repo.namcap("PKGBUILD", self.recipe)
        run.assert_called_once()

    def test_just_commands_use_the_shared_dispatcher(self):
        self.assertFalse((ROOT / "Makefile").exists())
        text = (ROOT / "justfile").read_text()
        self.assertIn("set positional-arguments", text)
        for command in ("build", "install", "chroot", "package-lint"):
            self.assertIn(f"{command} package:", text)
            self.assertIn(f'python support/repo.py {command} "$1"', text)

    def test_test_command_discovers_generic_and_recipe_local_suites(self):
        (self.recipe / "tests").mkdir()
        with (
            patch("sys.argv", ["repo.py", "test"]),
            patch.object(self.repo, "run") as run,
        ):
            self.repo.main()
        self.assertEqual(
            run.call_args_list,
            [
                call(sys.executable, "-B", "-m", "unittest", "discover", "-s", tests, "-v")
                for tests in (self.root / "tests", self.recipe / "tests")
            ],
        )

    def test_failed_suite_stops_test_dispatch(self):
        (self.recipe / "tests").mkdir()
        with (
            patch.object(
                self.repo, "run", side_effect=subprocess.CalledProcessError(1, "unittest")
            ) as run,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.repo.test()
        run.assert_called_once()


class RecipeContractTests(unittest.TestCase):
    def test_recipe_contracts_and_local_integrity(self):
        recipes = sorted((ROOT / "packages").glob("*/PKGBUILD"))
        self.assertTrue(recipes)
        for recipe in recipes:
            with self.subTest(recipe=recipe.parent.name):
                self.assertTrue(recipe.parent.name.endswith("-local"))
                fields = {}
                for line in (recipe.parent / ".SRCINFO").read_text().splitlines():
                    key, separator, value = line.strip().partition(" = ")
                    if separator:
                        fields.setdefault(key, []).append(value)
                self.assertEqual(fields["pkgname"], [recipe.parent.name])
                sources = fields["source"]
                checksums = fields["sha256sums"]
                self.assertEqual(len(sources), len(checksums))
                for source, checksum in zip(sources, checksums, strict=True):
                    self.assertRegex(checksum, r"^[a-f0-9]{64}$")
                    if "://" not in source:
                        self.assertEqual(
                            hashlib.sha256((recipe.parent / source).read_bytes()).hexdigest(),
                            checksum,
                        )
                text = recipe.read_text()
                self.assertNotIn("../../support", text)
                self.assertIn("check()", text)


if __name__ == "__main__":
    unittest.main()
