set positional-arguments

default:
    @just --list

check package='':
    python -B -m support.containers check ${1:+"$1"}

verify package='':
    python -B -m support.containers verify ${1:+"$1"}

discover package:
    python -B -m support.containers discover "$1"

srcinfo package:
    python -B -m support.containers srcinfo "$1"

build package:
    python -B -m support.containers build "$1"

test package:
    python -B -m support.containers test "$1"

install package:
    python -B -m support.install "$1"

new package:
    python -B -m support.scaffold "$1"
