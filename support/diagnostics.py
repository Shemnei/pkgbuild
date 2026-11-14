"""Keep captured subprocess diagnostics at every CLI failure boundary."""

import sys


def print_error(error):
    for field in ("stdout", "stderr"):
        output = getattr(error, field, None)
        if output:
            print(output, end="" if output.endswith("\n") else "\n", file=sys.stderr)
    print(f"error: {error}", file=sys.stderr)
