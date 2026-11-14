import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

from support.recipes import select, validate_integrity

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

    def test_removed_native_commands_are_rejected(self):
        for command in ("build", "install", "chroot", "srcinfo"):
            with (
                self.subTest(command=command),
                patch("sys.argv", ["repo.py", command, self.recipe.name]),
                patch("sys.stderr", new_callable=io.StringIO),
                self.assertRaises(SystemExit),
            ):
                self.repo.main()

    def test_verify_uses_only_the_selected_recipe(self):
        with (
            patch("sys.argv", ["repo.py", "verify", self.recipe.name]),
            patch.object(self.repo, "metadata") as metadata,
            patch.object(self.repo, "validate_integrity") as integrity,
            patch.object(self.repo, "run") as run,
        ):
            self.repo.main()
        metadata.assert_called_once_with(self.recipe)
        integrity.assert_called_once_with(self.recipe)
        run.assert_called_once_with("makepkg", "--verifysource", cwd=self.recipe)

    def test_discovery_checks_metadata_and_integrity_before_extracting_without_prepare(self):
        output = self.root / "output"
        sources = self.recipe / "src" / "upstream"
        sources.mkdir(parents=True)
        (sources / "server.js").write_text("checkForUpdates();\n")
        with (
            patch("sys.argv", ["repo.py", "discover", self.recipe.name, "--output", str(output)]),
            patch.object(self.repo, "metadata") as metadata,
            patch.object(self.repo, "validate_integrity") as integrity,
            patch.object(self.repo, "run") as run,
        ):
            calls = unittest.mock.Mock()
            calls.attach_mock(metadata, "metadata")
            calls.attach_mock(integrity, "integrity")
            calls.attach_mock(run, "run")
            self.repo.main()
        self.assertEqual(
            calls.mock_calls,
            [
                call.metadata(self.recipe),
                call.integrity(self.recipe),
                call.run("makepkg", "--nobuild", "--noprepare", "--nodeps", cwd=self.recipe),
            ],
        )
        report = json.loads((output / "discovery.json").read_text())
        self.assertEqual(report["findings"][0]["path"], "upstream/server.js")
        self.assertEqual(report["package"], self.recipe.name)

    def test_discovery_stops_on_metadata_integrity_or_extraction_failure(self):
        for boundary in ("metadata", "validate_integrity", "run"):
            with (
                self.subTest(boundary=boundary),
                patch("sys.argv", ["repo.py", "discover", self.recipe.name, "--output", "out"]),
                patch.object(self.repo, "metadata"),
                patch.object(self.repo, "validate_integrity"),
                patch.object(self.repo, "run"),
                patch.object(self.repo, boundary, side_effect=RuntimeError("fixture failure")),
                patch.object(self.repo, "scan_sources") as scan,
                self.assertRaisesRegex(RuntimeError, "fixture failure"),
            ):
                self.repo.main()
            scan.assert_not_called()

    def test_discovery_requires_selected_recipe_and_output(self):
        for args in ([], [self.recipe.name], ["--output", "out"]):
            with (
                self.subTest(args=args),
                patch("sys.argv", ["repo.py", "discover", *args]),
                patch("sys.stderr", new_callable=io.StringIO),
                patch.object(self.repo, "run") as run,
                self.assertRaises(SystemExit),
            ):
                self.repo.main()
            run.assert_not_called()

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

    def test_just_commands_use_containers_without_legacy_aliases(self):
        self.assertFalse((ROOT / "Makefile").exists())
        text = (ROOT / "justfile").read_text()
        self.assertIn("set positional-arguments", text)
        for command in ("build", "test", "srcinfo", "discover"):
            self.assertIn(f"{command} package:", text)
            self.assertIn(f'python -B -m support.containers {command} "$1"', text)
        self.assertIn('install package:\n    python -B -m support.install "$1"', text)
        for command in ("chroot", "package-lint", "container-check", "container-build"):
            self.assertNotIn(f"\n{command}", text)

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
        for directory in select(ROOT):
            with self.subTest(recipe=directory.name):
                validate_integrity(directory)
                text = (directory / "PKGBUILD").read_text()
                self.assertNotIn("../../support", text)
                self.assertIn("check()", text)


if __name__ == "__main__":
    unittest.main()
