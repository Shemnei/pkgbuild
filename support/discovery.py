"""Advisory source leads for human review, never a compliance verdict or patcher."""

import hashlib
import json
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_TEXT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    question: str


RULES = tuple(
    Rule(name, re.compile(pattern, re.IGNORECASE), question)
    for name, pattern, question in (
        (
            "updates",
            r"(?<![a-z0-9])(?:(?:auto|self)[_-]?(?:update|updating)\w*|check[_-]?(?:for[_-]?)?updates?"
            r"|update[_-]?(?:check|notifier|available)|new[_-]?version"
            r"|(?:download|install)[_-]?updates?|quit[_-]?and[_-]?install"
            r"|electron-updater|sparkle|appimageupdate|upgrade|uninstall)\b"
            r"|--(?:no-)?update(?:-to)?\b"
            r"|[/'\"]update(?:[/\s?'\"]|$)",
            "Can this check versions, prompt, download, replace, or remove managed software? "
            "Look for a supported disable switch and trace manual commands and HTTP routes too.",
        ),
        (
            "reporting",
            r"(?<![a-z0-9])(?:telemetry\w*|sentry|posthog\w*|segment|amplitude|mixpanel|bugsnag"
            r"|crashlytics|opentelemetry|otlp\w*|captureException|trackEvent"
            r"|(?:create|init|initialize)[_-]?(?:telemetry|analytics)|custom[_-]?track"
            r"|analytics[_-]?(?:occurred[_-]?at|word[_-]?count|counter[_-]?version)"
            r"|send[_-]?(?:usage|analytics)|crash[_-]?report\w*)(?![a-z0-9])"
            r"|/analytics(?=[/\s?'\"#]|$)",
            "Does this initialize or send outbound analytics, traces, or crash reports? "
            "Distinguish local statistics, service quota receipts, and remote erasure; inspect "
            "transitive exporters, custom callbacks, and saved opt-ins. Review metadata sent "
            "alongside ordinary requests, not only dedicated reporting endpoints.",
        ),
        (
            "software-installation",
            r"\b(?:npm|pnpm|yarn|bun|pip3?)\s+(?:install|add|update|upgrade)\b"
            r"|\b(?:npx|bunx|preinstall|postinstall"
            r"|install[_-]?(?:dependency|dependencies))\b"
            r"|\b(?:download|install|fetch|ensure|seed)[_-]?(?:binary|binaries|executables?)\b"
            r"|\b(?:gpu[_-]?)?binary[_-]?(?:manager|installer)\b"
            r"|--(?:no-)?remote-components\b"
            r"|\bcurl\b[^\n|]*\|\s*(?:bash|sh)\b",
            "Can this fetch or mutate executable dependencies at build time or runtime? "
            "Review lifecycle scripts, frozen resolution, writable binary caches, GPU packs, "
            "remote executable components, and system-tool alternatives. Distinguish model "
            "data from executable software.",
        ),
        (
            "child-processes",
            r"\b(?:child_process|spawn(?:Sync)?|execFile(?:Sync)?|subprocess|ProcessBuilder)\b"
            r"|\b(?:Command::new|std::process|os/exec)\b",
            "What runs here, and can the child install software or report independently? "
            "Follow the executable, arguments, and enforced environment.",
        ),
        (
            "background-entry-points",
            r"\b(?:setInterval|serviceWorker|registerSW|BackgroundFetch|cron)\b"
            r"|\b(?:periodic|background)[_-]?(?:update|check|task)\w*\b",
            "Can this start polling, reporting, or code replacement outside a manual command? "
            "Trace browser workers, startup registration, and scheduled tasks.",
        ),
        (
            "reporting-settings",
            r"(?<![a-z0-9])(?:set[_-]?)?(?:(?:enable|disable|allow|send)[_-]?(?:telemetry|analytics|usageReporting)"
            r"|(?:telemetry|analytics|usageReporting)[_-]?(?:enabled|disabled|opt[_-]?in)"
            r"|(?:insights|analytics|usage)[_-]?sync(?:[_-]?(?:enabled|disabled))?"
            r"|send[_-]?logs"
            r"|(?:installation|device|anonymous|tracking|distinct|session)[_-]?id)(?![a-z0-9])",
            "Can persisted settings or inherited environment reactivate a sender? "
            "Follow setters, sanitizers, identifiers, log-upload options, and the implementation "
            "boundary. Session/device IDs may authenticate requests or select hardware rather "
            "than track usage; trace their purpose before changing them.",
        ),
        (
            "integration-generators",
            r"--(?:completions?|generate-man\w*)\b"
            r"|\b(?:generate[_-]?(?:completions?|manpages?)|shtab|clap_complete"
            r"|clap_mangen|argcomplete|installShellCompletion)\b",
            "Is this an upstream-supported completion or manual generator? "
            "Run reviewed generators in build(), not package(), and test their output.",
        ),
    )
)
INTEGRATION_FILE = re.compile(
    r"\.(?:service|socket|timer|desktop|1|2|3|5|7|8)(?:\.in)?$|\.(?:bash|zsh|fish)$"
    r"|(?:^|/)(?:completions?|shell[_-]completions?)/|(?:^|/)_[a-z][a-z0-9-]*$"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    rule: str
    evidence: str


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    kind: str


@dataclass(frozen=True)
class Issue:
    path: str
    reason: str


@dataclass
class Scan:
    files: list[SourceFile] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    exclusions: list[Issue] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)


def source_kind(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if parts & {"test", "tests", "__tests__", "fixtures", "spec", "specs"} or re.search(
        r"(?:^test_|[._-](?:test|spec)\.)", name
    ):
        return "test"
    if parts & {"doc", "docs", "documentation"} or path.suffix.lower() in {".md", ".rst"}:
        return "documentation"
    if name in {
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "bun.lock",
        "yarn.lock",
        "pnpm-lock.yaml",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "requirements.txt",
        "composer.json",
        "composer.lock",
        "gemfile",
        "gemfile.lock",
    }:
        return "dependency-metadata"
    return "source"


def scan_sources(root: Path) -> Scan:
    """Scan all regular UTF-8 text, including vendored/hidden/generated files.

    Never follow symlinks or execute inputs. Traversal/read/decoding/size errors
    are collected so partial evidence can be saved before the command fails.
    """
    scan = Scan()

    def visit(path: Path) -> None:
        relative = path.relative_to(root)
        name = relative.as_posix()
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                scan.exclusions.append(Issue(name, "symlink; not followed"))
            elif stat.S_ISDIR(mode):
                if path.name == ".git":
                    scan.exclusions.append(Issue(name, "VCS internals"))
                else:
                    for child in sorted(path.iterdir()):
                        visit(child)
            elif not stat.S_ISREG(mode):
                raise ValueError("Unsupported non-regular source entry")
            else:
                with path.open("rb") as stream:
                    prefix = stream.read(min(8192, MAX_TEXT_BYTES + 1))
                    if b"\0" in prefix:
                        scan.exclusions.append(Issue(name, "binary (NUL bytes)"))
                        return
                    data = prefix + stream.read(MAX_TEXT_BYTES + 1 - len(prefix))
                if len(data) > MAX_TEXT_BYTES:
                    raise ValueError(f"Text exceeds {MAX_TEXT_BYTES} bytes; not truncated")
                if b"\0" in data:
                    scan.exclusions.append(Issue(name, "binary (NUL bytes)"))
                    return
                text = data.decode("utf-8")
                kind = source_kind(relative)
                scan.files.append(SourceFile(name, hashlib.sha256(data).hexdigest(), kind))
                if INTEGRATION_FILE.search(name):
                    scan.findings.append(
                        Finding(name, 1, kind, "integration-files", f"File: {name}"[:240])
                    )
                for number, line in enumerate(text.split("\n"), 1):
                    for rule in RULES:
                        match = rule.pattern.search(line)
                        if match:
                            start = max(0, match.start() - 80)
                            evidence = line[start : start + 240]
                            scan.findings.append(Finding(name, number, kind, rule.name, evidence))
        except (OSError, UnicodeError, ValueError) as error:
            scan.errors.append(Issue(name, str(error)))

    if root.is_symlink() or not root.is_dir():
        scan.errors.append(Issue(".", "Missing regular extracted-source directory"))
    else:
        visit(root)
    if not scan.files:
        scan.errors.append(Issue(".", "No regular UTF-8 text files scanned"))
    return scan


def write_report(scan: Scan, output: Path, package: str) -> None:
    """Preserve readable and structured evidence, including failed/partial scans."""
    questions = {rule.name: rule.question for rule in RULES}
    questions["integration-files"] = (
        "Is this file relevant to the shipped component? Review configuration, permissions, "
        "standard installation paths, and supported behavior; never auto-enable services."
    )
    report = {
        "schema_version": 1,
        "package": package,
        "status": "failed" if scan.errors else "completed",
        "scope": "Advisory source leads, not patch requirements or a compliance verdict. "
        "Paths are relative to the extracted src/ tree; line numbers are 1-based. "
        "Evidence excerpts are at most 240 characters; integration-file leads anchor at line 1.",
        "limitations": [
            "Heuristic text matching has false positives and false negatives; no call-graph analysis.",
            "All discovered regular UTF-8 text is scanned, regardless of shipped-component scope.",
            "Binary files, symlinks, and .git directories are excluded and listed explicitly.",
            f"Unreadable, non-UTF-8, unsupported entries or text over {MAX_TEXT_BYTES} bytes fail.",
            "Dependencies are not fetched; only dependency code present in the sources is scanned.",
            "No runtime, browser, child-process, or outbound-network behavior is established.",
        ],
        "questions": questions,
        **asdict(scan),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "discovery.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = [
        f"Patch-boundary discovery: {json.dumps(package)} — {report['status']}",
        report["scope"],
        f"Text files: {len(scan.files)}; leads: {len(scan.findings)}; "
        f"exclusions: {len(scan.exclusions)}; errors: {len(scan.errors)}",
        "",
        *report["limitations"],
    ]
    for name, question in questions.items():
        leads = [finding for finding in scan.findings if finding.rule == name]
        lines.extend(["", f"{name}: {len(leads)} lead(s)", question])
        for finding in leads:
            lines.append(
                f"  {json.dumps(finding.path)}:{finding.line} [{finding.kind}] "
                f"{json.dumps(finding.evidence)}"
            )
    for label, entries in (("Exclusions", scan.exclusions), ("Errors", scan.errors)):
        lines.extend(["", label])
        lines.extend(f"  {json.dumps(asdict(entry))}" for entry in entries)
    (output / "discovery.txt").write_text("\n".join(lines) + "\n")
    if scan.errors:
        raise RuntimeError(f"Incomplete discovery scan; see {output / 'discovery.json'}")
