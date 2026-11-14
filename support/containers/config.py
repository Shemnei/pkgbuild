"""Locked image configuration and source-only container snapshots."""

import datetime
import json
import re
import shutil

from support.recipes import declared_inputs, digest, select, side_inputs, srcinfo

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


def snapshot(root, destination, packages=None, *, include_sources=True, extra_inputs=()):
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
    if (root / ".github").exists() or (root / ".github").is_symlink():
        add(root / ".github")
    for path in extra_inputs:
        add(path)
    directories = select(root) if packages is None else [select(root, name)[0] for name in packages]
    for directory in directories:
        for name in RECIPE_FILES:
            path = directory / name
            if path.exists() or path.is_symlink():
                add(path)
        if (directory / "tests").exists():
            add(directory / "tests")
        if include_sources:
            for path in declared_inputs(directory, srcinfo((directory / ".SRCINFO").read_text())):
                add(path)
    manifest = {}
    for source in sorted(selected):
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(source.stat().st_mode & 0o777)
        manifest[str(relative)] = digest(target)
    return manifest


def metadata_snapshot(root, package, probe, destination, original):
    """Resolve only the file declarations requested by the isolated metadata probe."""
    fields = json.loads((probe / "metadata-inputs.json").read_text())
    if (
        not isinstance(fields, dict)
        or set(fields) != {"install", "changelog"}
        or any(
            not isinstance(values, list)
            or len(values) > 1
            or any(not isinstance(value, str) or not value for value in values)
            for values in fields.values()
        )
    ):
        raise ValueError("Invalid metadata input declarations")
    extra = list(side_inputs(select(root, package)[0], fields))
    updated = snapshot(root, destination, [package], include_sources=False, extra_inputs=extra)
    for name in ("PKGBUILD", ".SRCINFO"):
        key = f"packages/{package}/{name}"
        if updated.get(key) != original.get(key):
            raise ValueError(f"{name} changed during metadata discovery; rerun just srcinfo")
    return updated
