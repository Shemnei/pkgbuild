"""Run generic Arch validation jobs without host installs or privileged containers."""

import argparse
import datetime
import json
import os
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from support.artifacts import read_manifest
from support.diagnostics import print_error
from support.recipes import build_order, digest, select, srcinfo

from .config import image_lock, metadata_snapshot, snapshot
from .engine import Engine

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = Path(__file__).resolve().parent


def update_srcinfo(root, output, package, manifest):
    directory = select(root, package)[0]
    key = f"packages/{package}/PKGBUILD"
    if digest(directory / "PKGBUILD") != manifest[key]:
        raise ValueError("PKGBUILD changed during metadata generation; rerun just srcinfo")
    target = directory / ".SRCINFO"
    previous = manifest.get(f"packages/{package}/.SRCINFO")
    if target.is_symlink() or (digest(target) if target.is_file() else None) != previous:
        raise ValueError(".SRCINFO changed during metadata generation; rerun just srcinfo")
    source = output / package / ".SRCINFO"
    if source.is_symlink() or not source.is_file():
        raise ValueError("Missing regular generated metadata")
    actual = source.read_text()
    if srcinfo(actual).get("pkgname") != [package]:
        raise ValueError("Generated metadata does not match the selected package")
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as temporary:
        path = Path(temporary.name)
        try:
            temporary.write(actual)
            temporary.flush()
            path.chmod(0o644)
            path.replace(target)
        finally:
            path.unlink(missing_ok=True)


def validate(action, package, engine, output, root=ROOT, context=CONTEXT):
    lock = image_lock(context / "image.lock.json")
    order = build_order(root, package) if package and action not in {"srcinfo", "discover"} else []
    if action == "test":
        for name in order:
            if not (root / "packages" / name / "tests" / "installed.py").is_file():
                raise ValueError(f"Add packages/{name}/tests/installed.py for just test {package}")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "action": action,
        "package": package,
        "dependency_order": order,
        "engine": engine.name,
        "rootless": engine.rootless,
        "lock": lock,
        "images": {},
        "stages": [],
        "artifacts": {},
        "status": "failed",
    }
    print(f"Validation evidence: {output}", flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="pkgconf-container-") as temporary:
            inputs = Path(temporary) / "input"
            packages = order or ([package] if package else None)
            manifest = snapshot(root, inputs, packages, include_sources=action != "srcinfo")
            (output / "sources.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            )
            tools = engine.image(context, lock, "tools", output)
            report["images"]["tools"] = tools
            archives = {}

            def job(phase, name=None, image=tools["id"], commit=None):
                directory = output / (f"{phase}-{name}" if name else phase)
                stage = {
                    "phase": phase,
                    "package": name,
                    "returncode": None,
                    "output": str(directory.relative_to(output)),
                }
                report["stages"].append(stage)
                try:
                    status = engine.job(
                        image,
                        phase,
                        name,
                        lock["platform"],
                        inputs,
                        archives,
                        directory,
                        commit=commit,
                    )
                except BaseException as error:
                    stage["error"] = str(error)
                    raise
                stage["returncode"] = status
                return status, directory

            if action in {"check", "verify", "srcinfo", "discover"}:
                if action == "srcinfo":
                    status, probe = job("metadata-inputs", package)
                    if status:
                        raise RuntimeError("Container metadata input probe failed")
                    inputs = Path(temporary) / "metadata-input"
                    manifest = metadata_snapshot(root, package, probe, inputs, manifest)
                    (output / "sources.json").write_text(
                        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
                    )
                status, directory = job(action, package)
                if status:
                    raise RuntimeError(f"Container {action} failed")
                if action == "srcinfo":
                    update_srcinfo(root, directory, package, manifest)
            else:
                for name in order:
                    status, directory = job("build", name)
                    if status:
                        raise RuntimeError(f"Container build failed: {name}")
                    report["artifacts"][name] = read_manifest(directory)
                    archives[name] = directory
                # Preserve runtime evidence even when archive lint has findings.
                failures = []
                for name in order:
                    installed = engine.runtime_tag()
                    status, _ = job("lint-install", name, commit=installed)
                    if status:
                        failures.append(f"lint-install:{name}")
                        continue
                    try:
                        if job("lint", name, installed)[0]:
                            failures.append(f"lint:{name}")
                    finally:
                        engine.remove_image(installed)
                if action == "test":
                    runtime = engine.image(context, lock, "runtime", output)
                    report["images"]["runtime"] = runtime
                    for name in order:
                        installed = engine.runtime_tag()
                        status, _ = job("install", name, runtime["id"], commit=installed)
                        if status:
                            failures.append(f"install:{name}")
                            continue
                        try:
                            if job("smoke", name, installed)[0]:
                                failures.append(f"smoke:{name}")
                        finally:
                            engine.remove_image(installed)
                if failures:
                    raise RuntimeError(f"Failed validation gates: {', '.join(failures)}")
            report["status"] = "passed"
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["check", "verify", "srcinfo", "discover", "build", "test"]
    )
    parser.add_argument("package", nargs="?")
    parser.add_argument(
        "--engine", choices=["docker", "podman"], default=os.environ.get("CONTAINER_ENGINE")
    )
    parser.add_argument("--require-rootless", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action in {"srcinfo", "discover", "build", "test"} and args.package is None:
        parser.error("srcinfo/discover/build/test require a recipe directory name")
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / ".artifacts" / "containers" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    engine = Engine(args.engine, args.require_rootless)
    validate(args.action, args.package, engine, output.resolve())


def interrupted(signum, frame):
    # Unwind job cleanup and report writing instead of abandoning containers.
    raise KeyboardInterrupt(f"Interrupted by signal {signum}")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    try:
        main()
    except KeyboardInterrupt as error:
        print_error(error)
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print_error(error)
        sys.exit(1)
