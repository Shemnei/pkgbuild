"""Build and install a selected recipe locally with makepkg's normal pacman prompts."""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from support.diagnostics import print_error
from support.recipes import select

ROOT = Path(__file__).resolve().parents[1]


def install(root, package):
    if os.getuid() == 0:
        raise ValueError("Run just install as your normal user, not with sudo")
    if not sys.stdin.isatty():
        raise ValueError(
            "Host installation requires an interactive terminal for pacman confirmation"
        )
    directory = select(root, package)[0]
    makepkg = shutil.which("makepkg")
    if not makepkg or not shutil.which("pacman"):
        raise ValueError(
            "Local installation requires an Arch-compatible host with makepkg and pacman"
        )
    command = [makepkg, "--syncdeps", "--install"]
    print(f"[{directory}] {shlex.join(command)}", flush=True)
    subprocess.run(command, cwd=directory, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    args = parser.parse_args()
    install(ROOT, args.package)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt as error:
        print_error(error)
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print_error(error)
        sys.exit(1)
