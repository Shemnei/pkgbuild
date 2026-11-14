"""One exact, hashed archive manifest for build, lint, transport, and installation."""

import json
from pathlib import Path

from support.recipes import digest

MANIFEST = "artifacts.json"


def _archives(directory):
    return {path.name for path in directory.glob("*.pkg.tar.*") if not path.name.endswith(".sig")}


def _validate(directory, names):
    actual = _archives(directory)
    if len(names) != 1:
        raise ValueError("Each recipe must produce one package archive; disable debug splitting")
    if any(
        not isinstance(name, str)
        or Path(name).name != name
        or name not in actual
        or (directory / name).is_symlink()
        or not (directory / name).is_file()
        for name in names
    ):
        raise ValueError(f"Invalid package archive manifest in {directory}")
    if set(names) != actual:
        raise ValueError(f"Unexpected or missing package archives in {directory}")


def write_manifest(directory, expected):
    paths = [Path(value) for value in expected]
    if any(path.parent.resolve() != directory.resolve() for path in paths):
        raise ValueError("Build artifacts must be directly inside the output directory")
    names = [path.name for path in paths]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate expected package archives")
    _validate(directory, names)
    manifest = {name: digest(directory / name) for name in names}
    (directory / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def read_manifest(directory):
    path = directory / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing regular artifact manifest in {directory}")
    manifest = json.loads(path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid artifact manifest in {directory}")
    _validate(directory, manifest)
    if any(checksum != digest(directory / name) for name, checksum in manifest.items()):
        raise ValueError(f"Package archive checksum mismatch in {directory}")
    return manifest
