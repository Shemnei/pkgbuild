"""Run upstream lints with an exact, release-specific baseline, never a blanket skip."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Existing upstream v2.7.0 findings outside the packaging changes. These are
# matched by file, rule, severity, message, and every diagnostic location. Any
# changed, additional, or disappeared diagnostic requires explicit re-review.
BASELINE = {
    (
        "src/components/views/files/previews/OfficeArtifact.tsx",
        "react-hooks(exhaustive-deps)",
        "warning",
        "The ref's value `.current` is accessed directly in the effect cleanup function.",
        ((385, 12),),
    ),
    (
        "src/components/chat/useDraftStarters.ts",
        "react-hooks(exhaustive-deps)",
        "warning",
        "React Hook useMemo has a missing dependency: 'offersNewExtension'",
        ((174, 48), (186, 8)),
    ),
    (
        "src/components/inline-picker/useTextareaInlinePicker.tsx",
        "react-hooks(exhaustive-deps)",
        "warning",
        "React Hook useEffect contains a call to setState. Without a list of dependencies, "
        "this can lead to an infinite chain of updates.",
        ((47, 3),),
    ),
    (
        "src/components/layout/PluginPane.tsx",
        "react-hooks(exhaustive-deps)",
        "warning",
        "React Hook useEffect has a missing dependency: 'guest'",
        ((544, 10), (549, 6)),
    ),
}


def diagnostic_key(item):
    return (
        item["filename"],
        item["code"],
        item["severity"],
        item["message"],
        tuple((label["span"]["line"], label["span"]["column"]) for label in item["labels"]),
    )


def validate(diagnostics, baseline):
    keys = [diagnostic_key(item) for item in diagnostics]
    if len(keys) != len(set(keys)) or set(keys) != baseline:
        unexpected = set(keys) - baseline
        missing = baseline - set(keys)
        raise ValueError(
            f"Upstream lint baseline changed. Unexpected: {unexpected}; missing: {missing}"
        )


def lint(root):
    for workspace in ("web", "ui"):
        directory = root / "packages" / workspace
        # Explicit files bypass the directory walk's inherited packaging ignores.
        # Invoke the pinned tool directly: thousands of paths exceed Bash's
        # single-command-string limit when passed through nested `bun run` scripts.
        sources = sorted(
            str(path.relative_to(directory))
            for path in (directory / "src").rglob("*")
            if path.is_file()
            and path.suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
        )
        if not sources:
            raise ValueError(f"No upstream {workspace} sources found")
        try:
            result = subprocess.run(
                [
                    str(root / "node_modules/.bin/oxlint"),
                    "-c",
                    "../../oxlint.config.ts",
                    "--report-unused-disable-directives",
                    "--format=json",
                    *sources,
                ],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            print(error.stdout or "", file=sys.stderr)
            print(error.stderr or "", file=sys.stderr)
            raise
        report = json.loads(result.stdout)
        if report["number_of_files"] != len(sources):
            raise ValueError(
                f"Incomplete upstream {workspace} lint scan: {report['number_of_files']} "
                f"of {len(sources)} sources"
            )
        diagnostics = report["diagnostics"]
        validate(diagnostics, BASELINE if workspace == "ui" else set())
        print(f"{workspace}: no new findings ({len(diagnostics)} documented upstream warnings)")
        for item in diagnostics:
            print(f"  {item['filename']}: {item['message']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    lint(parser.parse_args().root)
