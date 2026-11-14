"""Create an explicitly unfinished, standalone recipe without overwriting user work."""

import argparse
import shutil
import tempfile
from pathlib import Path

from support.recipes import PACKAGE_NAME

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).resolve().parent / "templates" / "recipe"


def create(root, name):
    if not PACKAGE_NAME.fullmatch(name):
        raise ValueError("Use a valid package directory name ending in -local")
    parent = root / "packages"
    if parent.is_symlink():
        raise ValueError("The package directory may not be a symlink")
    parent.mkdir(exist_ok=True)
    destination = parent / name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to overwrite {destination}")
    with tempfile.TemporaryDirectory(prefix="pkgbuild-new-") as temporary:
        staged = Path(temporary) / name
        shutil.copytree(TEMPLATE, staged)
        for path in staged.rglob("*"):
            if path.is_file():
                path.write_text(path.read_text().replace("@PACKAGE@", name))
        # mkdir is exclusive; a concurrent creator can never be overwritten.
        destination.mkdir()
        for path in staged.iterdir():
            shutil.move(path, destination / path.name)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    args = parser.parse_args()
    directory = create(ROOT, args.package)
    print(f"Created unfinished recipe: {directory}")
    print(f"Replace every TODO, then run just srcinfo {args.package} and just check {args.package}")


if __name__ == "__main__":
    main()
