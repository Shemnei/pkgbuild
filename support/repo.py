"""Strict shared checks; recipes themselves do not depend on this module."""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT, capture=False):
    print(f"[{Path(cwd).relative_to(ROOT) or '.'}] {' '.join(map(str, args))}", flush=True)
    if shutil.which(str(args[0])) is None:
        raise RuntimeError(f"Required tool is missing: {args[0]}")
    return subprocess.run(
        list(map(str, args)), cwd=cwd, check=True, text=True, capture_output=capture
    ).stdout


def recipes():
    found = sorted((ROOT / "packages").glob("*/PKGBUILD"))
    if not found:
        raise RuntimeError("No recipes found")
    return [file.parent for file in found]


def select_package(name):
    directories = recipes()
    matches = [directory for directory in directories if directory.name == name]
    if len(matches) != 1:
        choices = ", ".join(directory.name for directory in directories)
        raise RuntimeError(f"Unknown recipe {name!r}; choose one of: {choices}")
    return matches[0]


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


def metadata(directory, write=False):
    actual = run("makepkg", "--printsrcinfo", cwd=directory, capture=True)
    target = directory / ".SRCINFO"
    if write:
        target.write_text(actual)
    elif not target.exists() or target.read_text() != actual:
        raise RuntimeError(f"Stale {target.relative_to(ROOT)}; run just srcinfo")


def lint():
    run("just", "--fmt", "--check")
    run("ruff", "check", ".")
    run("ruff", "format", "--check", ".")
    files = [
        file
        for folder in (ROOT / "support", ROOT / "tests", *recipes())
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
    for directory in recipes():
        metadata(directory)
        namcap("PKGBUILD", directory)


def test():
    # Separate processes keep identically named recipe test modules isolated.
    for directory in (ROOT, *recipes()):
        tests = directory / "tests"
        if directory == ROOT or tests.is_dir():
            run(sys.executable, "-B", "-m", "unittest", "discover", "-s", tests, "-v")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["lint", "test", "verify", "srcinfo", "build", "install", "chroot", "package-lint"],
    )
    parser.add_argument("package", nargs="?", default="")
    args = parser.parse_args()
    if args.command == "lint":
        lint()
    elif args.command == "test":
        test()
    elif args.command in {"verify", "srcinfo"}:
        for directory in recipes():
            if args.command == "verify":
                run("makepkg", "--verifysource", cwd=directory)
            else:
                metadata(directory, write=True)
    else:
        directory = select_package(args.package)
        if args.command in {"build", "install"}:
            # Only an explicit install may change host packages; keep pacman prompts.
            flags = ["--syncdeps", "--install"] if args.command == "install" else []
            run("makepkg", "--cleanbuild", "--log", *flags, cwd=directory)
        elif args.command == "chroot":
            run("extra-x86_64-build", cwd=directory)
        else:
            paths = run("makepkg", "--packagelist", cwd=directory, capture=True).splitlines()
            for path in paths:
                if not Path(path).is_file():
                    raise RuntimeError(f"Build first: {path}")
                namcap(path, directory)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
