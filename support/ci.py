"""Select CI targets without evaluating executable recipes on the runner."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from support.recipes import build_order, recipes

ROOT = Path(__file__).resolve().parents[1]


def selection(root, paths=None):
    available = recipes(root)
    shared = paths is None or any(not path.startswith("packages/") for path in paths)
    changed = (
        set(available)
        if shared
        else {path.split("/")[1] for path in paths if len(path.split("/")) > 2}
    )
    # Include consumers of runtime and build-only providers, transitively.
    packages = [name for name in available if changed.intersection(build_order(root, name))]
    return {"packages": packages, "framework": shared}


def changed_paths(root, base, head, *, pull_request=False):
    for ref in (base, head):
        if not re.fullmatch(r"[a-f0-9]{40}", ref):
            raise ValueError("CI diff refs must be full commit hashes")
    if base == "0" * 40:
        return None
    revision = f"{base}...{head}" if pull_request else f"{base}..{head}"
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", revision, "--"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--pull-request", action="store_true")
    args = parser.parse_args()
    if bool(args.base) != bool(args.head):
        parser.error("--base and --head must be supplied together")
    paths = (
        changed_paths(ROOT, args.base, args.head, pull_request=args.pull_request)
        if args.base
        else None
    )
    result = selection(ROOT, paths)
    print(json.dumps(result))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            for key, value in result.items():
                stream.write(f"{key}={json.dumps(value)}\n")


if __name__ == "__main__":
    main()
