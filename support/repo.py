"""Strict shared checks; recipes themselves do not depend on this module."""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from support.artifacts import read_manifest
from support.diagnostics import print_error
from support.discovery import scan_sources, write_report
from support.recipes import select, validate_integrity

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT, capture=False):
    print(f"[{Path(cwd).relative_to(ROOT) or '.'}] {' '.join(map(str, args))}", flush=True)
    if shutil.which(str(args[0])) is None:
        raise RuntimeError(f"Required tool is missing: {args[0]}")
    return subprocess.run(
        list(map(str, args)), cwd=cwd, check=True, text=True, capture_output=capture
    ).stdout


def namcap(target, cwd):
    output = run("namcap", target, cwd=cwd, capture=True)
    print(output, end="")
    # namcap can return zero even when it reports packaging errors.
    findings = [line for line in output.splitlines() if re.search(r"(?:^|\s)[EW]:", line)]
    path = cwd / "namcap.toml"
    exceptions = {}
    # Source-recipe lint never accepts archive-specific exceptions.
    if str(target) != "PKGBUILD" and path.exists():
        config = tomllib.loads(path.read_text())
        if set(config) != {"pkgname", "pkgver", "build_packages", "exceptions"}:
            raise RuntimeError(f"Invalid archive-lint exception fields in {path}")
        metadata = run("bsdtar", "-xOf", target, ".PKGINFO", ".BUILDINFO", cwd=cwd, capture=True)
        fields = dict(line.split(" = ", 1) for line in metadata.splitlines() if " = " in line)
        if any(config[key] != fields.get(key) for key in ("pkgname", "pkgver")):
            raise RuntimeError(f"Archive-lint exceptions do not match package version in {target}")
        packages = config["build_packages"]
        if (
            not isinstance(packages, list)
            or any(not isinstance(item, str) or not item.strip() for item in packages)
            or len(packages) != len(set(packages))
        ):
            raise RuntimeError(f"Invalid archive-lint build-package pins in {path}")
        installed = set(re.findall(r"^installed = (.+)$", metadata, re.MULTILINE))
        if not set(packages) <= installed:
            raise RuntimeError(f"Archive-lint build-package pins do not match {target}")
        if not isinstance(config["exceptions"], list) or not config["exceptions"]:
            raise RuntimeError(f"Archive-lint exceptions must be a nonempty list in {path}")
        for item in config["exceptions"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"diagnostic", "reason"}
                or not isinstance(item["diagnostic"], str)
                or not item["diagnostic"].startswith(f"{config['pkgname']} W: ")
                or not isinstance(item["reason"], str)
                or not item["reason"].strip()
                or "\n" in item["diagnostic"]
                or "\r" in item["diagnostic"]
                or item["diagnostic"] in exceptions
            ):
                raise RuntimeError(f"Invalid or duplicate archive-lint exception in {path}")
            exceptions[item["diagnostic"]] = item["reason"]
    if len(findings) != len(set(findings)) or set(findings) != set(exceptions):
        raise RuntimeError(
            f"Unresolved namcap finding or stale exception in {target}: "
            f"unexpected={set(findings) - set(exceptions)}; "
            f"missing={set(exceptions) - set(findings)}"
        )
    for diagnostic, reason in exceptions.items():
        print(f"Reviewed archive-lint exception: {diagnostic}\n  Reason: {reason}")


def metadata(directory):
    actual = run("makepkg", "--printsrcinfo", cwd=directory, capture=True)
    target = directory / ".SRCINFO"
    if not target.exists() or target.read_text() != actual:
        raise RuntimeError(f"Stale {target.relative_to(ROOT)}; run just srcinfo {directory.name}")


def lint(package=None):
    directories = select(ROOT, package)
    run("just", "--fmt", "--check")
    run("ruff", "check", ".")
    run("ruff", "format", "--check", ".")
    files = [
        file
        for folder in (ROOT / "support", ROOT / "tests", *directories)
        for file in folder.rglob("*")
        if file.is_file()
        and not {"src", "pkg", "__pycache__"}.intersection(file.relative_to(folder).parts)
    ]
    scripts = set()
    for file in sorted(set(files)):
        with file.open("rb") as stream:
            prefix = stream.read(128)
        if prefix.startswith(b"#!"):
            scripts.add(file)
        if file.suffix == ".json":
            json.loads(file.read_text())
        elif file.suffix == ".toml":
            tomllib.loads(file.read_text())
        elif (
            file.name == "PKGBUILD"
            or file.suffix in {".sh", ".install"}
            or prefix.startswith((b"#!/bin/bash", b"#!/usr/bin/env bash"))
        ):
            run("bash", "-n", file)
            run("shellcheck", "--shell=bash", file)
            run("shfmt", "-d", "-i", "2", "-ci", file)
        elif file.suffix == ".mjs":
            run("node", "--check", file)
        elif file.suffix == ".desktop":
            run("desktop-file-validate", file)
    tomllib.loads((ROOT / "pyproject.toml").read_text())
    spelling_files = [ROOT / "AGENTS.md", ROOT / "README.md"] + [
        file
        for file in sorted(set(files))
        if file.suffix in {".py", ".mjs", ".md", ".service"}
        or file.name == "PKGBUILD"
        or file in scripts
    ]
    run("codespell", *spelling_files)
    run("typos", *spelling_files)
    for directory in directories:
        metadata(directory)
        validate_integrity(directory)
        namcap("PKGBUILD", directory)


def test(package=None):
    # Separate processes keep identically named recipe test modules isolated.
    for directory in (ROOT, *select(ROOT, package)):
        tests = directory / "tests"
        if directory == ROOT or tests.is_dir():
            run(sys.executable, "-B", "-m", "unittest", "discover", "-s", tests, "-v")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["check", "lint", "test", "verify", "discover", "package-lint"]
    )
    parser.add_argument("package", nargs="?")
    parser.add_argument("--archives", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "check":
        lint(args.package)
        test(args.package)
    elif args.command == "lint":
        lint(args.package)
    elif args.command == "test":
        test(args.package)
    elif args.command == "verify":
        for directory in select(ROOT, args.package):
            metadata(directory)
            validate_integrity(directory)
            run("makepkg", "--verifysource", cwd=directory)
    elif args.command == "discover":
        if not args.package or args.output is None:
            parser.error("discover requires a package and --output directory")
        directory = select(ROOT, args.package)[0]
        metadata(directory)
        validate_integrity(directory)
        # makepkg verifies and extracts sources, but never calls prepare/build/check/package.
        # The PKGBUILD itself is trusted recipe code and still evaluated as the builder.
        run("makepkg", "--nobuild", "--noprepare", "--nodeps", cwd=directory)
        write_report(scan_sources(directory / "src"), args.output, args.package)
    else:
        if not args.package or args.archives is None:
            parser.error("package-lint requires a package and --archives directory")
        directory = select(ROOT, args.package)[0]
        for name in read_manifest(args.archives):
            namcap(args.archives / name, directory)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print_error(error)
        sys.exit(1)
