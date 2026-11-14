import io
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from support import install


class HostInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.recipe = self.root / "packages" / "example-local"
        self.recipe.mkdir(parents=True)
        (self.recipe / "PKGBUILD").write_text("pkgname=example-local\n")
        self.real_run = subprocess.run
        for target, kwargs in (
            ("os.getuid", {"return_value": 1000}),
            ("sys.stdin.isatty", {"return_value": True}),
            ("shutil.which", {"side_effect": lambda name: f"/usr/bin/{name}"}),
        ):
            patcher = patch(f"support.install.{target}", **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("support.install.subprocess.run")
        self.run_command = patcher.start()
        self.addCleanup(patcher.stop)

    def test_local_makepkg_builds_and_installs_selected_recipe_with_normal_prompts(self):
        with redirect_stdout(io.StringIO()):
            install.install(self.root, "example-local")
        self.run_command.assert_called_once_with(
            ["/usr/bin/makepkg", "--syncdeps", "--install"],
            cwd=self.recipe,
            check=True,
        )
        self.assertFalse((self.root / ".artifacts").exists())

    def test_preflight_refuses_root_noninteractive_and_missing_tools(self):
        cases = (
            ("os.getuid", 0, "normal user"),
            ("sys.stdin.isatty", False, "interactive terminal"),
            ("shutil.which", None, "makepkg and pacman"),
        )
        for target, value, message in cases:
            with (
                self.subTest(target=target),
                patch(f"support.install.{target}", return_value=value),
                self.assertRaisesRegex(ValueError, message),
            ):
                install.install(self.root, "example-local")
        self.run_command.assert_not_called()

    def test_invalid_recipe_never_starts_makepkg(self):
        for name in ("missing-local", "../example-local"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unknown local"):
                install.install(self.root, name)
        self.run_command.assert_not_called()

    def test_makepkg_failure_propagates_without_retry_or_fallback(self):
        failure = subprocess.CalledProcessError(17, "makepkg")
        self.run_command.side_effect = failure
        with (
            redirect_stdout(io.StringIO()),
            self.assertRaises(subprocess.CalledProcessError) as error,
        ):
            install.install(self.root, "example-local")
        self.assertIs(error.exception, failure)
        self.run_command.assert_called_once()

    def test_real_subprocess_passes_recipe_directory_flags_and_failure(self):
        binary = self.root / "makepkg"
        record = self.root / "invocation"
        for status in (0, 17):
            binary.write_text(
                f'#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > {shlex.quote(str(record))}\n'
                f"exit {status}\n"
            )
            binary.chmod(0o755)
            with (
                self.subTest(status=status),
                patch.object(
                    install.shutil,
                    "which",
                    side_effect=lambda name: (
                        str(binary) if name == "makepkg" else "/usr/bin/pacman"
                    ),
                ),
                patch.object(install.subprocess, "run", side_effect=self.real_run),
                redirect_stdout(io.StringIO()),
            ):
                if status:
                    with self.assertRaises(subprocess.CalledProcessError) as error:
                        install.install(self.root, "example-local")
                    self.assertEqual(error.exception.returncode, status)
                else:
                    install.install(self.root, "example-local")
            self.assertEqual(
                record.read_text().splitlines(),
                [str(self.recipe), "--syncdeps", "--install"],
            )
