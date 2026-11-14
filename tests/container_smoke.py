"""Opt-in real-engine transport test: python -B -m tests.container_smoke.

Uses a tiny synthetic recipe rather than downloading or building an application.
All inputs, reports, and failed evidence remain under .artifacts/framework-smoke/.
"""

import datetime
import io
import json
import shutil
import tarfile
import uuid

from support.containers.__main__ import ROOT, validate
from support.containers.config import ROOT_FILES
from support.containers.engine import Engine
from support.recipes import digest

PKGBUILD = """# shellcheck disable=SC2034,SC2154
pkgname=fixture-local
pkgver=1
pkgrel=1
pkgdesc='Generic framework transport fixture'
arch=('x86_64')
url='https://example.invalid'
license=('MIT')
depends=('glibc')
options=('!debug')
install=fixture.install
changelog=history.txt
source_x86_64=('payload.txt' 'fixture.c' 'LICENSE' 'audit.tar')
sha512sums_x86_64=('@PAYLOAD@' '@PROGRAM@' '@LICENSE@' '@AUDIT@')

prepare() {
  printf '%s\\n' '// prepared source' >"$srcdir/audit/server.js"
}

build() {
  # shellcheck disable=SC2086
  cc $CPPFLAGS $CFLAGS "$srcdir/fixture.c" -o fixture-local $LDFLAGS
}

check() {
  [[ $(<"$srcdir/payload.txt") == 'reviewed fixture payload' ]]
  [[ $(<"$srcdir/audit/server.js") == '// prepared source' ]]
  ./fixture-local | cmp - "$srcdir/payload.txt"
}

package() {
  install -Dm755 "$srcdir/fixture-local" "$pkgdir/usr/bin/fixture-local"
  install -Dm644 "$srcdir/payload.txt" "$pkgdir/usr/share/fixture-local/payload.txt"
  install -Dm644 "$srcdir/payload.txt" "$pkgdir/usr/share/doc/fixture-local/README.md"
  install -Dm644 "$srcdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
"""

LICENSE = """MIT License

Copyright (c) 2026 Local package maintainers

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

INSTALLED = """import os
import subprocess
from pathlib import Path

if os.getuid() == 0:
    raise RuntimeError('Installed test ran as root')
if Path('/usr/share/fixture-local/payload.txt').read_text() != 'reviewed fixture payload\\n':
    raise RuntimeError('Missing installed fixture payload')
if not Path('/usr/share/doc/fixture-local/README.md').is_file():
    raise RuntimeError('Package documentation was discarded during installation')
result = subprocess.run(['/usr/bin/fixture-local'], text=True, capture_output=True, check=True)
if result.stdout != 'reviewed fixture payload\\n':
    raise RuntimeError('Installed executable did not run')
result = subprocess.run(['pacman', '-Qc', 'fixture-local'], text=True, capture_output=True, check=True)
if 'fixture history' not in result.stdout:
    raise RuntimeError('Missing packaged changelog')
"""


def main():
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence = ROOT / ".artifacts" / "framework-smoke" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    root = evidence / "repository"
    root.mkdir(parents=True)
    for name in ROOT_FILES:
        shutil.copyfile(ROOT / name, root / name)
    for name in ("support", "tests"):
        shutil.copytree(
            ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    directory = root / "packages" / "fixture-local"
    (directory / "tests").mkdir(parents=True)
    (directory / "payload.txt").write_text("reviewed fixture payload\n")
    (directory / "fixture.c").write_text(
        '#include <stdio.h>\nint main(void) { puts("reviewed fixture payload"); return 0; }\n'
    )
    (directory / "LICENSE").write_text(LICENSE)
    with tarfile.open(directory / "audit.tar", "w") as archive:
        data = b"checkForUpdates();\nSentry.init();\n"
        member = tarfile.TarInfo("audit/server.js")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    (directory / "PKGBUILD").write_text(
        PKGBUILD.replace("@PAYLOAD@", digest(directory / "payload.txt", "sha512"))
        .replace("@PROGRAM@", digest(directory / "fixture.c", "sha512"))
        .replace("@LICENSE@", digest(directory / "LICENSE", "sha512"))
        .replace("@AUDIT@", digest(directory / "audit.tar", "sha512"))
    )
    (directory / "fixture.install").write_text("post_install() { :; }\n")
    (directory / "history.txt").write_text("fixture history\n")
    (directory / "tests" / "installed.py").write_text(INSTALLED)
    engine = Engine()
    validate("srcinfo", "fixture-local", engine, evidence / "metadata", root=root)
    validate("discover", "fixture-local", engine, evidence / "discovery", root=root)
    report = json.loads((evidence / "discovery/discover-fixture-local/discovery.json").read_text())
    if report["status"] != "completed" or {
        lead["rule"] for lead in report["findings"] if lead["path"] == "audit/server.js"
    } != {"updates", "reporting"}:
        raise RuntimeError("Discovery lost unpatched-source leads or ran prepare()")
    if (directory / "src").exists():
        raise RuntimeError("Discovery extracted sources on the host")
    validate("test", "fixture-local", engine, evidence / "installed", root=root)
    original = (directory / ".SRCINFO").read_text()
    (directory / "PKGBUILD").write_text("pkgname=fixture-local\n)\n")
    try:
        validate("srcinfo", "fixture-local", engine, evidence / "failed-metadata", root=root)
    except RuntimeError:
        console = evidence / "failed-metadata" / "metadata-inputs-fixture-local" / "console.log"
        if "syntax error" not in console.read_text():
            raise RuntimeError("Captured metadata diagnostics were lost") from None
        if (directory / ".SRCINFO").read_text() != original:
            raise RuntimeError("Failed generation changed host metadata") from None
    else:
        raise RuntimeError("Invalid metadata unexpectedly passed")
    print(f"Framework smoke passed; evidence: {evidence}")


if __name__ == "__main__":
    main()
