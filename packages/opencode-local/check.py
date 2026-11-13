"""Validate the reviewed source boundaries and the resulting OpenCode executable."""

import argparse
import shutil
import subprocess
from pathlib import Path

from prepare import EXPECTED
from smoke import smoke


def check(root):
    local_test = "packages/cli/test/local-policy.test.mjs"
    shutil.copyfile(Path(__file__).with_name("policy.test.mjs"), root / local_test)
    sources = [name for name in EXPECTED if name.endswith(".ts")]
    commands = [
        ["bun", "run", "prettier", "--check", *sources],
        ["bun", "run", "oxlint", "--deny-warnings", "-D", "correctness", *sources, local_test],
        ["bun", "run", "ast-grep", "scan", "-c", "script/ast-grep/sgconfig.yml", *sources],
        [
            "bun",
            "run",
            "--cwd",
            "packages/cli",
            "test",
            "./test/local-policy.test.mjs",
            "./test/app-archive.test.ts",
            "./src/services/updater.test.ts",
        ],
        ["bun", "run", "--cwd", "packages/cli", "typecheck:tsc"],
    ]
    for command in commands:
        print(" ".join(command), flush=True)
        subprocess.run(command, cwd=root, check=True)
    smoke(root / "packages/cli/dist/cli-linux-x64/bin/opencode")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    check(parser.parse_args().root)
