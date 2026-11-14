"""Copy the installed production graph without re-resolving the pnpm lockfile."""

import json
import os
import shutil
import sys
from pathlib import Path


def resolve_package(parent, name):
    if not name or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError(f"Invalid dependency name: {name}")
    for directory in (parent, *parent.parents):
        candidate = directory / "node_modules" / name
        if (candidate / "package.json").is_file():
            return candidate.resolve()
    return None


def deploy(source, destination):
    source = source.resolve()
    if destination.exists():
        raise ValueError(f"Refusing to overwrite staging directory: {destination}")
    destination.mkdir(parents=True)
    ignore = shutil.ignore_patterns(
        "node_modules", ".git", "__tests__", "tests", "test", "*.test.*", "*.spec.*", "*.map"
    )
    for name in ("dist", "bin", "schemas"):
        shutil.copytree(source / name, destination / name, ignore=ignore)
    for name in ("LICENSE", "README.md"):
        shutil.copy2(source / name, destination / name)
    manifest = json.loads((source / "package.json").read_text())
    for key in ("devDependencies", "scripts", "packageManager"):
        manifest.pop(key, None)
    (destination / "package.json").write_text(json.dumps(manifest, indent=2) + "\n")
    installed = {source: destination}

    def stage_dependencies(package, target):
        metadata = json.loads((package / "package.json").read_text())
        required = metadata.get("dependencies", {})
        optional = metadata.get("optionalDependencies", {})
        peers = metadata.get("peerDependencies", {})
        peer_meta = metadata.get("peerDependenciesMeta", {})
        for name in sorted(required.keys() | optional.keys() | peers.keys()):
            # Inquirer's optional type-only peer is resolved from the dev graph.
            if (
                name == "@types/node"
                and name not in required
                and name in peers
                and peer_meta.get(name, {}).get("optional")
            ):
                continue
            resolved = resolve_package(package, name)
            if resolved is None:
                if name in optional or peer_meta.get(name, {}).get("optional"):
                    continue
                raise ValueError(f"Missing production dependency {name} for {package}")
            if not resolved.is_relative_to(source):
                raise ValueError(f"Dependency escaped source tree: {resolved}")
            if resolved not in installed:
                store = destination / "node_modules/.store" / f"{len(installed):05d}"
                installed[resolved] = store
                shutil.copytree(resolved, store, ignore=ignore)
                stage_dependencies(resolved, store)
            link = target / "node_modules" / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(installed[resolved], link.parent))

    stage_dependencies(source, destination)
    for link in destination.rglob("*"):
        if link.is_symlink() and not link.resolve(strict=True).is_relative_to(
            destination.resolve()
        ):
            raise ValueError(f"Dependency escaped staging: {link}")


if __name__ == "__main__":
    deploy(Path(sys.argv[1]), Path(sys.argv[2]))
