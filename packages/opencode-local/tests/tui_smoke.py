"""Render the real standalone TUI in a private terminal and exit with Ctrl+C."""

import errno
import fcntl
import os
import pty
import selectors
import signal
import struct
import tempfile
import termios
import time
from pathlib import Path


def smoke(binary, log):
    with tempfile.TemporaryDirectory(prefix="opencode-tui-") as home:
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": home,
            "TMPDIR": home,
            "LANG": "C.UTF-8",
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "DO_NOT_TRACK": "1",
            "BUN_CONFIG_NO_TELEMETRY": "1",
        }
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(home)
            os.execve(binary, [str(binary), "--standalone"], environment)
        output = bytearray()
        status = None
        exiting = False
        try:
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            with selectors.DefaultSelector() as selector:
                selector.register(master, selectors.EVENT_READ)
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    if selector.select(timeout=0.5):
                        try:
                            chunk = os.read(master, 65536)
                        except OSError as error:
                            if error.errno == errno.EIO:
                                break
                            raise
                        if not chunk:
                            break
                        output.extend(chunk)
                        # Answer the renderer's ordinary terminal capability queries.
                        for query, response in (
                            (b"\x1b[c", b"\x1b[?1;2c"),
                            (b"\x1b[6n", b"\x1b[1;1R"),
                            (b"\x1b]11;?", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
                        ):
                            if query in chunk:
                                os.write(master, response)
                        if not exiting and b"Ask anything" in output and b"2.2.7" in output:
                            os.write(master, b"\x03")
                            exiting = True
                            deadline = time.monotonic() + 10
                    done, code = os.waitpid(pid, os.WNOHANG)
                    if done:
                        status = code
                        break
        finally:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if status is None:
                _, status = os.waitpid(pid, 0)
            os.close(master)
            Path(log).write_bytes(output)
        if not exiting or os.waitstatus_to_exitcode(status) != 0:
            raise ValueError(f"TUI did not render and exit cleanly; see {log}")
    print("Standalone TUI rendered its prompt/version and exited cleanly with Ctrl+C")
