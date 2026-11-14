"""Canonical recipe inputs, integrity policy, and local dependency plans."""

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9@+._-]*-local")
DEPENDENCY = re.compile(r"[a-zA-Z0-9@+._-]+(?:[<>=]{1,2}[^\s/]+)?")
STRONG_HASHES = {
    "sha256sums": "sha256",
    "sha384sums": "sha384",
    "sha512sums": "sha512",
    "b2sums": "blake2b",
}


def digest(path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def recipes(root):
    if (root / "packages").is_symlink():
        raise ValueError("The package directory may not be a symlink")
    found = {
        path.parent.name: path.parent for path in sorted((root / "packages").glob("*/PKGBUILD"))
    }
    if not found:
        raise ValueError("No package recipes found")
    for name, directory in found.items():
        if directory.is_symlink() or (directory / "PKGBUILD").is_symlink():
            raise ValueError("Recipe directories and PKGBUILDs may not be symlinks")
        if not PACKAGE_NAME.fullmatch(name):
            raise ValueError("Recipe names must be valid package names ending in -local")
    return found


def select(root, name=None):
    available = recipes(root)
    if name is None:
        return list(available.values())
    if name not in available:
        raise ValueError(f"Unknown local recipe: {name}")
    return [available[name]]


@dataclass(frozen=True)
class Validation:
    local_dependencies: tuple[str, ...] = ()
    local_build_dependencies: tuple[str, ...] = ()
    test_dependencies: tuple[str, ...] = ()


def recipe_config(directory):
    path = directory / "validation.toml"
    config = tomllib.loads(path.read_text()) if path.exists() else {}
    if set(config) - Validation.__dataclass_fields__.keys():
        raise ValueError(f"Unknown validation settings in {path}")
    result = {}
    for key in Validation.__dataclass_fields__:
        values = config.get(key, [])
        pattern = DEPENDENCY if key == "test_dependencies" else PACKAGE_NAME
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not pattern.fullmatch(value) for value in values
        ):
            raise ValueError(f"Invalid {key} in {path}")
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate {key} in {path}")
        result[key] = tuple(values)
    if set(result["local_dependencies"]) & set(result["local_build_dependencies"]):
        raise ValueError(f"Local dependencies cannot be both runtime and build-only in {path}")
    return Validation(**result)


def _closure(root, targets, *, build):
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
        dependencies = config.local_dependencies
        if build:
            dependencies += config.local_build_dependencies
        for dependency in dependencies:
            visit(dependency)
        visiting.pop()
        ordered.append(name)

    for target in targets:
        visit(target)
    return ordered


def build_order(root, package):
    return _closure(root, [package], build=True)


def runtime_closure(root, package):
    return _closure(root, [package], build=False)


def build_installation(root, package):
    config = recipe_config(select(root, package)[0])
    return _closure(root, config.local_dependencies + config.local_build_dependencies, build=False)


def srcinfo(text):
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition(" = ")
        if separator:
            fields.setdefault(key, []).append(value)
    return fields


def local_input(directory, value):
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Local input escapes recipe: {value}")
    path = directory / relative
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError(f"Symlinked local input: {value}")
    if not path.is_file():
        raise ValueError(f"Local input must be a regular file: {value}")
    return path


def source_groups(fields):
    for key, values in fields.items():
        if key == "source" or key.startswith("source_"):
            yield key.removeprefix("source"), values


def declared_inputs(directory, fields):
    for _, values in source_groups(fields):
        for value in values:
            source = value.split("::", 1)[-1]
            if "://" not in source:
                yield local_input(directory, source)
    yield from side_inputs(directory, fields)


def side_inputs(directory, fields):
    """makepkg validates these file declarations even during --printsrcinfo."""
    for key in ("install", "changelog"):
        for value in fields.get(key, []):
            yield local_input(directory, value)


def validate_integrity(directory):
    fields = srcinfo((directory / ".SRCINFO").read_text())
    # This repository deliberately supports one output package per recipe.
    if fields.get("pkgname") != [directory.name]:
        raise ValueError(f"Expected one output package named {directory.name}")
    if any(
        "SKIP" in values
        for key, values in fields.items()
        if re.fullmatch(r"(?:cksums|md5sums|sha\d+sums|b2sums)(?:_.*)?", key)
    ):
        raise ValueError(f"Skipped source integrity is not allowed in {directory}")
    for suffix, sources in source_groups(fields):
        hashes = {key: fields[key + suffix] for key in STRONG_HASHES if key + suffix in fields}
        if not hashes:
            raise ValueError(f"Missing strong checksums for source{suffix} in {directory}")
        for key, checksums in hashes.items():
            algorithm = STRONG_HASHES[key]
            length = hashlib.new(algorithm).digest_size * 2
            if len(checksums) != len(sources):
                raise ValueError(f"Mismatched {key}{suffix} in {directory}")
            for source, checksum in zip(sources, checksums, strict=True):
                if not re.fullmatch(rf"[a-f0-9]{{{length}}}", checksum):
                    raise ValueError(f"Invalid strong checksum for {source} in {directory}")
                value = source.split("::", 1)[-1]
                if (
                    "://" not in value
                    and digest(local_input(directory, value), algorithm) != checksum
                ):
                    raise ValueError(f"Local source checksum mismatch: {source}")
    # All metadata-declared local inputs receive the same boundary checks.
    list(declared_inputs(directory, fields))
    return fields
