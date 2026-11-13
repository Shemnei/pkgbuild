set positional-arguments

default:
    @just --list

check: lint test

lint:
    python support/repo.py lint

test:
    python -B support/repo.py test

verify:
    python support/repo.py verify

srcinfo:
    python support/repo.py srcinfo

build package:
    python support/repo.py build "$1"

install package:
    python support/repo.py install "$1"

chroot package:
    python support/repo.py chroot "$1"

package-lint package:
    python support/repo.py package-lint "$1"

container-check:
    python -B -m support.containers check

container-build package:
    python -B -m support.containers build "$1"

container-test package:
    python -B -m support.containers test "$1"
