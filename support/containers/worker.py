"""In-container coordinator. Only package setup runs as container root."""

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

from support.artifacts import read_manifest, write_manifest
from support.diagnostics import print_error
from support.recipes import (
    DEPENDENCY,
    build_installation,
    build_order,
    recipe_config,
    runtime_closure,
    select,
    srcinfo,
    validate_integrity,
)

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


def check_pacman_policy():
    config = run("pacman-conf", capture=True).stdout
    directives = {line.partition("=")[0].strip() for line in config.splitlines()}
    forbidden = {
        "NoExtract",
        "DisableSandbox",
        "DisableSandboxFilesystem",
        "DisableSandboxSyscalls",
    }
    if directives & forbidden:
        raise ValueError(
            f"Image disables package contents or download sandboxing: {directives & forbidden}"
        )


def install_archives(names):
    archives = []
    for name in names:
        directory = ARCHIVES / name
        archives.extend(directory / filename for filename in read_manifest(directory))
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
    fields = validate_integrity(directory)
    install_archives(build_installation(root, package))
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
    write_manifest(OUTPUT, expected.stdout.splitlines())


def install(root, package, *, test_tools=True):
    install_archives(runtime_closure(root, package))
    if test_tools:
        install_missing(recipe_config(root / "packages" / package).test_dependencies)


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
    parser.add_argument(
        "phase",
        choices=[
            "check",
            "verify",
            "discover",
            "metadata-inputs",
            "srcinfo",
            "build",
            "lint",
            "lint-install",
            "install",
            "smoke",
        ],
    )
    parser.add_argument("package", nargs="?")
    args = parser.parse_args()
    if os.getuid() != 0:
        raise ValueError("The coordinator needs container root for package setup")
    check_pacman_policy()
    if args.phase in {"build", "lint", "lint-install", "install", "smoke"}:
        build_order(INPUT, args.package)
    root = INPUT if args.phase in {"install", "lint-install"} else prepare_workspace()
    if args.phase in {"check", "verify", "discover"}:
        arguments = [args.package] if args.package else []
        if args.phase == "discover":
            arguments.extend(["--output", str(OUTPUT)])
        run(
            "/usr/bin/python",
            "-B",
            "-m",
            "support.repo",
            args.phase,
            *arguments,
            cwd=root,
            builder=True,
        )
    elif args.phase == "metadata-inputs":
        directory = select(root, args.package)[0]
        result = run(
            "bash",
            "--noprofile",
            "--norc",
            "-ec",
            'source ./PKGBUILD >/dev/null; printf "%s\\0%s\\0" "${install-}" "${changelog-}"',
            cwd=directory,
            builder=True,
            capture=True,
        )
        values = result.stdout.split("\0")
        if len(values) != 3 or values[-1]:
            raise ValueError("Invalid metadata input probe result")
        fields = {
            key: [value] if value else []
            for key, value in zip(("install", "changelog"), values[:2], strict=True)
        }
        (OUTPUT / "metadata-inputs.json").write_text(json.dumps(fields) + "\n")
    elif args.phase == "srcinfo":
        for directory in select(root, args.package):
            result = run("makepkg", "--printsrcinfo", cwd=directory, builder=True, capture=True)
            if srcinfo(result.stdout).get("pkgname") != [directory.name]:
                raise ValueError(f"Expected one output package named {directory.name}")
            target = OUTPUT / directory.name / ".SRCINFO"
            target.parent.mkdir()
            target.write_text(result.stdout)
    elif args.phase == "build":
        build(root, args.package)
    elif args.phase == "lint":
        # Reuse the repository's strict namcap handling rather than another filter.
        run(
            "/usr/bin/python",
            "-B",
            "-m",
            "support.repo",
            "package-lint",
            args.package,
            "--archives",
            ARCHIVES / args.package,
            cwd=root,
            builder=True,
        )
    elif args.phase in {"install", "lint-install"}:
        install(root, args.package, test_tools=args.phase == "install")
    else:
        smoke(root, args.package)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print_error(error)
        sys.exit(1)
