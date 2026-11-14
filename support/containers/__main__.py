"""Run generic Arch validation jobs without host installs or privileged containers."""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from .config import dependency_order, image_lock, snapshot
from .engine import Engine

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = Path(__file__).resolve().parent


def archives_in(directory):
    paths = [
        path for path in sorted(directory.glob("*.pkg.tar.*")) if not path.name.endswith(".sig")
    ]
    if not paths or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError(f"No regular package archives produced in {directory}")
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def validate(action, package, engine, output, root=ROOT, context=CONTEXT):
    lock = image_lock(context / "image.lock.json")
    order = dependency_order(root, package) if action != "check" else []
    if action == "test":
        for name in order:
            if not (root / "packages" / name / "tests" / "installed.py").is_file():
                raise ValueError(f"Add packages/{name}/tests/installed.py for container-test")
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
        with tempfile.TemporaryDirectory(prefix="pkgbuild-container-") as temporary:
            inputs = Path(temporary) / "input"
            manifest = snapshot(root, inputs)
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

            if action == "check":
                if job("check")[0]:
                    raise RuntimeError("Container repository checks failed")
            else:
                for name in order:
                    status, directory = job("build", name)
                    if status:
                        raise RuntimeError(f"Container build failed: {name}")
                    report["artifacts"][name] = archives_in(directory)
                    archives[name] = directory
                # Preserve runtime evidence even when archive lint has findings.
                failures = [name for name in order if job("lint", name)[0]]
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
    parser.add_argument("action", choices=["check", "build", "test"])
    parser.add_argument("package", nargs="?")
    parser.add_argument(
        "--engine", choices=["docker", "podman"], default=os.environ.get("CONTAINER_ENGINE")
    )
    parser.add_argument("--require-rootless", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (args.action == "check") != (args.package is None):
        parser.error("check takes no package; build/test require a recipe directory name")
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / ".artifacts" / "containers" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    engine = Engine(args.engine, args.require_rootless)
    validate(args.action, args.package, engine, output.resolve())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
