"""Stage only the installed production dependency closure, preserving resolution."""

import argparse
import json
import os
import shutil
from pathlib import Path


def resolve_package(parent, name):
    """Resolve package directories, including packages that hide package.json exports."""
    if not name or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError(f"Invalid dependency name: {name}")
    for base in (parent, *parent.parents):
        candidate = base / "node_modules" / name
        if (candidate / "package.json").is_file():
            return candidate.resolve()
    return None


def copy_package(source, destination):
    manifest = source / "package.json"
    name = json.loads(manifest.read_text()).get("name") if manifest.is_file() else None
    common = shutil.ignore_patterns(
        "node_modules",
        ".git",
        "__tests__",
        "*.test.*",
        "*.spec.*",
        "*.tsbuildinfo",
        "node_gyp_bins",
        "obj.target",
        ".deps",
        "*.o",
    )

    def ignore(directory, entries):
        excluded = set(common(directory, entries))
        relative = Path(directory).relative_to(source)
        # This recipe targets Linux x86_64/glibc. Keep the source-built terminal
        # addon, not a fallback prebuild or foreign-platform executables.
        if name == "node-pty" and relative == Path("."):
            excluded.update({"prebuilds", "third_party"})
        elif name == "bun-pty" and relative == Path("rust-pty/target/release"):
            excluded.update(set(entries) - {"librust_pty.so"})
        return excluded

    shutil.copytree(
        source,
        destination,
        symlinks=False,
        ignore=ignore,
    )


def deploy(source, destination):
    source = source.resolve()
    if destination.exists():
        raise ValueError(f"Refusing to overwrite an existing staging directory: {destination}")
    destination.mkdir(parents=True)
    # Match upstream's published web package, without development sources/config.
    for name in ("bin", "server", "dist", "public"):
        copy_package(source / name, destination / name)
    for name in ("package.json", "README.md"):
        shutil.copy2(source / name, destination / name)

    installed = {source: destination}

    def stage_dependencies(package, target):
        manifest = json.loads((package / "package.json").read_text())
        required = manifest.get("dependencies", {})
        optional = manifest.get("optionalDependencies", {})
        peers = manifest.get("peerDependencies", {})
        peer_meta = manifest.get("peerDependenciesMeta", {})
        for name in sorted(required.keys() | optional.keys() | peers.keys()):
            # v2.7.0's web model-downloader imports this only on Windows.
            # Linux uses the system tar/bzip2 tools; do not ship an unused 7za.
            if package == source and name == "7zip-bin":
                continue
            resolved = resolve_package(package, name)
            if resolved is None:
                if name in optional or peer_meta.get(name, {}).get("optional"):
                    continue
                raise ValueError(f"Missing production dependency {name} for {package.name}")
            if resolved not in installed:
                store = destination / "node_modules" / ".store" / f"{len(installed):05d}"
                installed[resolved] = store  # Register before recursing through cycles.
                copy_package(resolved, store)
                stage_dependencies(resolved, store)
            link = target / "node_modules" / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(installed[resolved], link.parent))

    stage_dependencies(source, destination)
    # All dependency links are relative and point inside the staged closure.
    for link in destination.rglob("*"):
        if link.is_symlink() and not link.resolve(strict=True).is_relative_to(
            destination.resolve()
        ):
            raise ValueError(f"Dependency escaped staging: {link}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    deploy(args.source, args.destination)
