"""Recipe contracts and a source-only snapshot shared by all container jobs."""

import datetime
import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path

PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9@+._-]*-local")
DEPENDENCY = re.compile(r"[a-zA-Z0-9@+._-]+(?:[<>=]{1,2}[^\s/]+)?")
ROOT_FILES = ("AGENTS.md", "README.md", "justfile", "pyproject.toml", ".editorconfig", ".gitignore")
RECIPE_FILES = ("PKGBUILD", ".SRCINFO", "README.md", "AGENTS.md", "validation.toml", "namcap.toml")


def image_lock(path):
    lock = json.loads(path.read_text())
    if set(lock) != {"base_image", "platform", "arch_snapshot"}:
        raise ValueError("Unexpected image lock fields")
    if not re.fullmatch(r"docker.io/library/archlinux@sha256:[a-f0-9]{64}", lock["base_image"]):
        raise ValueError("The Arch base image must be pinned by SHA-256 digest")
    if lock["platform"] != "linux/amd64":
        raise ValueError("Only the locked linux/amd64 Arch image is supported")
    if datetime.date.fromisoformat(lock["arch_snapshot"]).isoformat() != lock["arch_snapshot"]:
        raise ValueError("Invalid Arch archive date")
    return lock


def recipes(root):
    if (root / "packages").is_symlink():
        raise ValueError("The package directory may not be a symlink")
    found = {
        path.parent.name: path.parent for path in sorted((root / "packages").glob("*/PKGBUILD"))
    }
    if not found:
        raise ValueError("No package recipes found")
    if any(path.is_symlink() for path in found.values()):
        raise ValueError("Recipe directories may not be symlinks")
    if any(not PACKAGE_NAME.fullmatch(name) for name in found):
        raise ValueError("Recipe directory names must be valid package names ending in -local")
    return found


def recipe_config(directory):
    path = directory / "validation.toml"
    config = tomllib.loads(path.read_text()) if path.exists() else {}
    if set(config) - {"local_dependencies", "local_build_dependencies", "test_dependencies"}:
        raise ValueError(f"Unknown validation settings in {path}")
    result = {}
    for key in ("local_dependencies", "local_build_dependencies", "test_dependencies"):
        values = config.get(key, [])
        pattern = DEPENDENCY if key == "test_dependencies" else PACKAGE_NAME
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not pattern.fullmatch(value) for value in values
        ):
            raise ValueError(f"Invalid {key} in {path}")
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate {key} in {path}")
        result[key] = values
    if set(result["local_dependencies"]) & set(result["local_build_dependencies"]):
        raise ValueError(f"Local dependencies cannot be both runtime and build-only in {path}")
    return result


def dependency_order(root, package, runtime=False):
    available = recipes(root)
    ordered, visiting = [], []

    def visit(name):
        if name not in available:
            raise ValueError(f"Unknown local recipe: {name}")
        if name in visiting:
            raise ValueError(f"Local dependency cycle: {' -> '.join([*visiting, name])}")
        if name in ordered:
            return
        visiting.append(name)
        config = recipe_config(available[name])
        dependencies = config["local_dependencies"]
        if not runtime:
            dependencies = [*dependencies, *config["local_build_dependencies"]]
        for dependency in dependencies:
            visit(dependency)
        visiting.pop()
        ordered.append(name)

    visit(package)
    return ordered


def srcinfo(text):
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition(" = ")
        if separator:
            fields.setdefault(key, []).append(value)
    return fields


def snapshot(root, destination):
    """Copy declared inputs, never downloads, build trees, VCS state, or user dotfiles."""
    selected = set()

    def add(path):
        if not path.is_relative_to(root):
            raise ValueError(f"Snapshot input escapes the repository: {path}")
        if path.is_symlink() or any(
            parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)
        ):
            raise ValueError(f"Snapshot inputs may not be symlinks: {path}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.name not in {"__pycache__", ".ruff_cache", ".pytest_cache"}:
                    add(child)
        elif path.is_file():
            if path.suffix != ".pyc":
                selected.add(path)
        else:
            raise ValueError(f"Missing or unsupported snapshot input: {path}")

    for name in ROOT_FILES:
        add(root / name)
    for name in ("support", "tests"):
        add(root / name)
    for directory in recipes(root).values():
        for name in RECIPE_FILES:
            path = directory / name
            if path.exists() or path.is_symlink():
                add(path)
        if (directory / "tests").exists():
            add(directory / "tests")
        fields = srcinfo((directory / ".SRCINFO").read_text())
        for key, values in fields.items():
            if key == "source" or key.startswith("source_"):
                for value in values:
                    source = value.split("::", 1)[-1]
                    if "://" in source:
                        continue
                    relative = Path(source)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError(f"Local source escapes recipe: {source}")
                    path = directory / relative
                    # Check every parent too; a symlinked directory must not leak host files.
                    if any(
                        parent.is_symlink() for parent in (path, *path.parents) if parent != root
                    ):
                        raise ValueError(f"Symlinked local source: {source}")
                    if not path.is_file():
                        raise ValueError(f"Local source must be a regular file: {source}")
                    add(path)
    manifest = {}
    for source in sorted(selected):
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(source.stat().st_mode & 0o777)
        manifest[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    return manifest
