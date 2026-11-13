"""Enforce dependency reporting policy and build against Arch's SQLite."""

import hashlib
import json
import sys
from pathlib import Path

EXPORTERS = {
    "index.mjs": "e9b209df1966d8767b0a023350e6bb3a0786eeeba5a4e0c05dacafe25e534fa5",
    "node.mjs": "153181fa6e4d8384b7c823d73c5d559341eae87c1ee3d047681aee9317ba32fe",
}
DISABLED = """// Reporting cannot be enabled by options, environment, or customTrack.
export async function createTelemetry() {
  return { publish: async () => {} };
}
export async function getTelemetryAuthConfig() {
  return {};
}
"""
SQLITE_BINDING = "2a48a110d17a692e769e4747dfea3b4471842ba86501db7cdcda33f8dfcb2921"


def prepare_dependencies(root):
    package = root / "node_modules/@better-auth/telemetry"
    if json.loads((package / "package.json").read_text())["version"] != "1.7.14":
        raise ValueError("Unaudited Better Auth telemetry version")
    for name, expected in EXPORTERS.items():
        if hashlib.sha256((package / "dist" / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Unaudited Better Auth exporter: {name}")
    sqlite = root / "node_modules/better-sqlite3"
    binding = sqlite / "binding.gyp"
    if (
        json.loads((sqlite / "package.json").read_text())["version"] != "12.11.1"
        or hashlib.sha256(binding.read_bytes()).hexdigest() != SQLITE_BINDING
    ):
        raise ValueError("Unaudited better-sqlite3 build definition")
    # Upstream's --sqlite3 flag selects an amalgamation, not a shared system library.
    # Keep its addon target/compiler settings, replacing only the SQLite dependency.
    text = binding.read_text().replace(
        "'dependencies': ['deps/sqlite3.gyp:sqlite3'],",
        "'libraries': ['-lsqlite3'], 'defines': ['SQLITE_ENABLE_COLUMN_METADATA'],",
    )
    binding.write_text(text)
    for name in EXPORTERS:
        (package / "dist" / name).write_text(DISABLED)


if __name__ == "__main__":
    prepare_dependencies(Path(sys.argv[1]).resolve())
