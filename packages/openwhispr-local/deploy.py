"""Stage only upstream's runtime sources; dependency installation stays in build()."""

import json
import shutil
import sys
from pathlib import Path

DIRECTORIES = (
    "src/dist",
    "src/helpers",
    "src/workers",
    "src/config",
    "src/constants",
    "src/locales",
    "src/hooks",
    "src/models",
    "src/types",
    "src/utils",
    "src/assets",
    "resources/bin",
)
FILES = (
    "main.js",
    "preload.js",
    "package.json",
    "package-lock.json",
    "src/updater.js",
    "src/components.json",
    "src/services/localReasoningBridge.js",
    "src/services/transcriptionBaseUrl.ts",
    "src/stores/policyRules.ts",
)


def deploy(root, target):
    # Refuse to reuse a possibly stale tree, including with makepkg --noextract.
    target.mkdir()
    for name in DIRECTORIES:
        shutil.copytree(root / name, target / name)
    for name in FILES:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)
    package = json.loads((target / "package.json").read_text())
    lock = json.loads((target / "package-lock.json").read_text())
    # Do not ship the scaffolding CLI or obsolete software downloaders. Retain
    # lockfile resolutions for the runtime dependencies, without another resolver.
    for manifest in (package, lock["packages"][""]):
        for name in ("shadcn-ui", "electron-updater", "ffmpeg-static"):
            manifest["dependencies"].pop(name)
    (target / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    # Installation scripts do not belong in an installed desktop application.
    package.pop("scripts")
    (target / "package.json").write_text(json.dumps(package, indent=2) + "\n")


def finalize(target, licenses):
    """Retain runtime modules and notices, not dependency CLIs or build fixtures."""
    modules = target / "node_modules"
    licenses.mkdir()
    for file in sorted(modules.rglob("*")):
        if file.is_file() and file.name.upper().split(".")[0] in {
            "LICENSE",
            "LICENCE",
            "COPYING",
            "NOTICE",
            "OFL",
        }:
            destination = licenses / file.relative_to(modules)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, destination)
    # These are audited build-only/test/foreign-language trees, not JS runtime APIs.
    for name in (
        ".bin",
        "typescript",
        "prebuild-install",
        "flatted/python",
        "better-sqlite3/deps",
        "better-sqlite3/src",
        "ehbp/dist/esm/test",
        "onnxruntime-node/bin/napi-v6/darwin",
        "onnxruntime-node/bin/napi-v6/win32",
        "onnxruntime-node/bin/napi-v6/linux/arm64",
    ):
        shutil.rmtree(modules / name)
    (modules / "better-sqlite3/binding.gyp").unlink()
    # npm also creates .bin trees inside dependencies with nested resolutions.
    for directory in sorted(modules.rglob(".bin"), key=lambda path: len(path.parts), reverse=True):
        shutil.rmtree(directory)
    for manifest in sorted(modules.rglob("package.json")):
        package = json.loads(manifest.read_text())
        commands = package.get("bin", {})
        if isinstance(commands, str):
            commands = {package.get("name", "cli"): commands}
        for name in commands.values():
            file = (manifest.parent / name).resolve()
            if not file.is_relative_to(manifest.parent.resolve()):
                raise ValueError(f"Dependency CLI escapes its package: {file}")
            if file.is_file():
                # Preserve any dual-purpose module; it is private Electron code,
                # not a standalone system-Node executable. Drop its CLI shebang below.
                main = manifest.parent / package.get("main", "index.js")
                if file != main.resolve() and not package.get("exports"):
                    file.unlink()
        for field in ("bin", "scripts"):
            package.pop(field, None)
        manifest.write_text(json.dumps(package, indent=2) + "\n")
    for file in modules.rglob("*"):
        if file.is_file() and file.suffix in {".js", ".mjs", ".cjs", ".ts"}:
            data = file.read_bytes()
            if data.startswith(b"#!"):
                file.write_bytes(data.partition(b"\n")[2])
            file.chmod(0o644)
    for directory in sorted(modules.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
            directory.rmdir()


if __name__ == "__main__":
    if sys.argv[1] == "--finalize":
        finalize(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
    else:
        deploy(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
