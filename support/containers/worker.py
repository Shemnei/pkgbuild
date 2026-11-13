"""In-container coordinator. Only package setup runs as container root."""

import argparse
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

from .config import DEPENDENCY, dependency_order, recipe_config, srcinfo

INPUT = Path("/input")
ARCHIVES = Path("/archives")
OUTPUT = Path("/output")
WORK = Path("/work")


def builder_env():
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/builder",
        "TMPDIR": str(WORK / "tmp"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PKGDEST": str(OUTPUT),
        "LOGDEST": str(OUTPUT),
        "DO_NOT_TRACK": "1",
    }


def run(
    *arguments, cwd=WORK, builder=False, capture=False, check=True, environment=None, timeout=None
):
    print(f"[{'builder' if builder else 'setup'}] {' '.join(map(str, arguments))}", flush=True)
    identity = pwd.getpwnam("builder")
    options = (
        {
            "user": identity.pw_uid,
            "group": identity.pw_gid,
            "extra_groups": [],
            "env": {**builder_env(), **(environment or {})},
        }
        if builder
        else {}
    )
    return subprocess.run(
        list(map(str, arguments)),
        cwd=cwd,
        text=True,
        capture_output=capture,
        check=check,
        timeout=timeout,
        **options,
    )


def install_missing(dependencies):
    dependencies = sorted(set(dependencies))
    if not dependencies:
        return
    if any(not DEPENDENCY.fullmatch(item) for item in dependencies):
        raise ValueError("Invalid package dependency from recipe metadata")
    result = run("pacman", "-T", "--", *dependencies, capture=True, check=False)
    if result.returncode not in {0, 127}:
        raise RuntimeError(f"pacman dependency check failed: {result.stderr}")
    missing = result.stdout.splitlines()
    if missing:
        if any(item not in dependencies for item in missing):
            raise ValueError("Unexpected dependency-check output")
        # Dated archive mirrors can pause longer than libcurl's low-speed cutoff.
        # Keep a finite overall deadline, all signature checks, and the download sandbox.
        run(
            "pacman",
            "-S",
            "--disable-download-timeout",
            "--needed",
            "--noconfirm",
            "--",
            *missing,
            timeout=900,
        )


def install_archives(root, package, include_target):
    names = dependency_order(root, package, runtime=include_target)
    if not include_target:
        names = names[:-1]
    archives = []
    for name in names:
        files = sorted((ARCHIVES / name).glob("*.pkg.tar.*"))
        files = [path for path in files if not path.name.endswith(".sig")]
        if not files or any(path.is_symlink() or not path.is_file() for path in files):
            raise ValueError(f"Missing regular package archives for {name}")
        archives.extend(files)
    if archives:
        run(
            "pacman",
            "-U",
            "--disable-download-timeout",
            "--needed",
            "--noconfirm",
            "--",
            *archives,
            timeout=900,
        )


def prepare_workspace():
    temporary = WORK / "tmp"
    temporary.mkdir()
    shutil.chown(temporary, user="builder", group="builder")
    root = WORK / "repository"
    shutil.copytree(INPUT, root)
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in input snapshot: {path}")
        shutil.chown(path, user="builder", group="builder")
    return root


def build(root, package):
    directory = root / "packages" / package
    # Never source a PKGBUILD as root, even to learn its dependencies.
    metadata = run("makepkg", "--printsrcinfo", cwd=directory, builder=True, capture=True).stdout
    if metadata != (directory / ".SRCINFO").read_text():
        raise ValueError(f"Stale metadata for {package}; run just srcinfo")
    fields = srcinfo(metadata)
    install_archives(root, package, include_target=False)
    dependencies = []
    for key, values in fields.items():
        category, _, architecture = key.partition("_")
        if category in {"depends", "makedepends", "checkdepends"} and architecture in {
            "",
            "x86_64",
        }:
            dependencies.extend(values)
    install_missing(dependencies)
    run("makepkg", "--verifysource", cwd=directory, builder=True)
    run("makepkg", "--cleanbuild", "--log", cwd=directory, builder=True)
    expected = run("makepkg", "--packagelist", cwd=directory, builder=True, capture=True)
    for filename in expected.stdout.splitlines():
        path = Path(filename)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(OUTPUT):
            raise ValueError(f"Invalid build artifact: {filename}")


def install(root, package):
    install_archives(root, package, include_target=True)
    install_missing(recipe_config(root / "packages" / package)["test_dependencies"])


def smoke(root, package):
    fields = srcinfo((root / "packages" / package / ".SRCINFO").read_text())
    run("pacman", "-Q", "--", *fields["pkgname"])
    test = root / "packages" / package / "tests" / "installed.py"
    if not test.is_file():
        raise ValueError(f"Missing installed-package test: {test}")
    # The runtime image has neither the build tree nor build-only dependencies.
    directory = WORK / "smoke"
    directory.mkdir()
    shutil.chown(directory, user="builder", group="builder")
    run("/usr/bin/python", "-B", test, cwd=directory, builder=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["check", "build", "lint", "install", "smoke"])
    parser.add_argument("package", nargs="?")
    args = parser.parse_args()
    if os.getuid() != 0:
        raise ValueError("The coordinator needs container root for package setup")
    if args.phase != "check":
        dependency_order(INPUT, args.package)
    root = INPUT if args.phase == "install" else prepare_workspace()
    if args.phase == "check":
        run("just", "check", cwd=root, builder=True)
    elif args.phase == "build":
        build(root, args.package)
    elif args.phase == "lint":
        # Reuse the repository's strict namcap handling rather than another filter.
        run(
            "/usr/bin/python",
            "-B",
            "support/repo.py",
            "package-lint",
            args.package,
            cwd=root,
            builder=True,
            environment={"PKGDEST": str(ARCHIVES / args.package)},
        )
    elif args.phase == "install":
        install(root, args.package)
    else:
        smoke(root, args.package)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
