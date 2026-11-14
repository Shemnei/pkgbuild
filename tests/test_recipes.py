import hashlib
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from support.artifacts import read_manifest, write_manifest
from support.diagnostics import print_error
from support.recipes import digest, recipes, validate_integrity
from support.scaffold import create


class RecipeIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "packages" / "example-local"
        self.directory.mkdir(parents=True)
        (self.directory / "PKGBUILD").write_text("pkgname=example-local\n")
        self.source = self.directory / "input.patch"
        self.source.write_text("reviewed source\n")

    def metadata(self, text):
        (self.directory / ".SRCINFO").write_text("pkgname = example-local\n" + text)

    def test_architecture_only_sources_accept_all_supported_strong_hashes(self):
        for key, algorithm in (
            ("sha256sums", "sha256"),
            ("sha384sums", "sha384"),
            ("sha512sums", "sha512"),
            ("b2sums", "blake2b"),
        ):
            with self.subTest(algorithm=algorithm):
                self.metadata(
                    f"source_x86_64 = input.patch\n{key}_x86_64 = {digest(self.source, algorithm)}\n"
                )
                validate_integrity(self.directory)

    def test_architecture_specific_drift_is_rejected(self):
        self.metadata(
            "source = https://example.invalid/release\nsha256sums = " + "0" * 64 + "\n"
            f"source_x86_64 = input.patch\nsha256sums_x86_64 = {digest(self.source)}\n"
        )
        self.source.write_text("changed source\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            validate_integrity(self.directory)

    def test_missing_weak_skipped_and_mismatched_checksums_fail(self):
        for checksum in ("", "md5sums = " + "0" * 32, "sha256sums = SKIP", "sha256sums = 123"):
            with self.subTest(checksum=checksum):
                self.metadata(f"source = input.patch\n{checksum}\n")
                with self.assertRaises(ValueError):
                    validate_integrity(self.directory)

    def test_single_output_policy_is_explicit(self):
        self.metadata("pkgname = second-local\n")
        with self.assertRaisesRegex(ValueError, "one output package"):
            validate_integrity(self.directory)

    def test_discovery_rejects_invalid_names_and_symlinked_pkgbuilds(self):
        invalid = self.directory.parent / "invalid"
        invalid.mkdir()
        (invalid / "PKGBUILD").write_text("pkgname=invalid\n")
        with self.assertRaisesRegex(ValueError, "ending in -local"):
            recipes(self.root)
        (invalid / "PKGBUILD").unlink()
        (self.directory / "PKGBUILD").unlink()
        (self.directory / "PKGBUILD").symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            recipes(self.root)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.archive = self.directory / "example-local-1-1-any.pkg.tar.zst"
        self.archive.write_bytes(b"archive bytes")

    def test_manifest_round_trip_and_changed_archive_rejection(self):
        expected = {self.archive.name: hashlib.sha256(b"archive bytes").hexdigest()}
        self.assertEqual(write_manifest(self.directory, [self.archive]), expected)
        self.assertEqual(read_manifest(self.directory), expected)
        self.archive.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            read_manifest(self.directory)

    def test_unexpected_archives_fail_at_build_and_consumption(self):
        write_manifest(self.directory, [self.archive])
        (self.directory / "unreviewed-1-1-any.pkg.tar.zst").write_bytes(b"extra")
        for action in (
            lambda: write_manifest(self.directory, [self.archive]),
            lambda: read_manifest(self.directory),
        ):
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                action()

    def test_empty_missing_outside_and_symlinked_archives_fail(self):
        for expected in ([], [self.directory.parent / self.archive.name]):
            with self.assertRaises(ValueError):
                write_manifest(self.directory, expected)
        write_manifest(self.directory, [self.archive])
        self.archive.unlink()
        self.archive.symlink_to("/etc/passwd")
        with self.assertRaises(ValueError):
            read_manifest(self.directory)

    def test_implicit_debug_or_split_output_is_rejected(self):
        extra = self.directory / "example-local-debug-1-1-any.pkg.tar.zst"
        extra.write_bytes(b"debug archive")
        with self.assertRaisesRegex(ValueError, "one package archive"):
            write_manifest(self.directory, [self.archive, extra])


class DiagnosticsTests(unittest.TestCase):
    def test_captured_failures_retain_both_streams(self):
        error = subprocess.CalledProcessError(
            1,
            ["makepkg", "--printsrcinfo"],
            output="partial metadata",
            stderr="PKGBUILD syntax error\n",
        )
        output = io.StringIO()
        with redirect_stderr(output):
            print_error(error)
        self.assertIn("partial metadata\n", output.getvalue())
        self.assertIn("PKGBUILD syntax error\n", output.getvalue())
        self.assertIn("exit status 1", output.getvalue())


class ScaffoldTests(unittest.TestCase):
    def test_new_recipe_is_standalone_explicitly_unfinished_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = create(root, "example-local")
            text = (directory / "PKGBUILD").read_text()
            self.assertIn("pkgname='example-local'", text)
            self.assertNotIn("@PACKAGE@", text)
            self.assertNotIn("support/", text)
            self.assertIn("return 1", text)
            self.assertFalse((directory / ".SRCINFO").exists())
            test = directory / "tests" / "installed.py"
            result = subprocess.run(
                ["python", "-B", test], capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("NotImplementedError", result.stderr)
            with self.assertRaises(FileExistsError):
                create(root, "example-local")
            self.assertEqual((directory / "PKGBUILD").read_text(), text)
            with self.assertRaises(ValueError):
                create(root, "../unsafe-local")
