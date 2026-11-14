import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from support import discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "src"
        self.root.mkdir()
        self.output = self.root.parent / "output"

    def source(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_leads_cover_policy_boundaries_without_treating_networking_as_reporting(self):
        text = "\n".join(
            (
                "checkForUpdates();",
                "Sentry.init();",
                'run("npm install tool");',
                'spawn("child");',
                "navigator.serviceWorker.register('/worker.js');",
                "settings.telemetryEnabled = saved.installationId;",
                "generate_completions();",
                "fetch(providerUrl); // ordinary user-requested networking",
                "const localTokenCount = 10;",
                'server.post("/api/update", update);',
                'command("upgrade");',
            )
        )
        path = self.source("backend/entry.js", text)
        scan = discovery.scan_sources(self.root)
        self.assertFalse(scan.errors)
        self.assertEqual(
            {finding.rule for finding in scan.findings}, {r.name for r in discovery.RULES}
        )
        self.assertFalse(any(finding.line in {8, 9} for finding in scan.findings))
        self.assertTrue(
            any(finding.line == 10 and finding.rule == "updates" for finding in scan.findings)
        )
        self.assertTrue(
            any(finding.line == 11 and finding.rule == "updates" for finding in scan.findings)
        )
        self.assertEqual(scan.files[0].sha256, hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(path.read_text(), text)

    def test_test_documentation_and_dependency_leads_remain_distinguishable(self):
        for name, expected in (
            ("tests/reporting.py", "test"),
            ("src/reporting.test.ts", "test"),
            ("docs/privacy.txt", "documentation"),
            ("README.md", "documentation"),
            ("vendor/library/package.json", "dependency-metadata"),
            ("Cargo.lock", "dependency-metadata"),
            (".hidden/worker.js", "source"),
            ("dist/bundle.js", "source"),
            ("node_modules/library/index.js", "source"),
        ):
            self.source(name, "telemetry()\n")
            with self.subTest(name=name):
                scan = discovery.scan_sources(self.root)
                finding = next(finding for finding in scan.findings if finding.path == name)
                self.assertEqual(finding.kind, expected)

    def test_integration_files_are_leads_even_without_keyword_content(self):
        names = (
            "units/app.service",
            "units/app.timer.in",
            "man/app.1",
            "app.desktop",
            "app.fish",
            "app.bash",
            "_app",
            "completions/app",
        )
        for name in names:
            self.source(name, "upstream content\n")
        scan = discovery.scan_sources(self.root)
        self.assertEqual(len(scan.findings), len(names))
        self.assertTrue(all(f.rule == "integration-files" and f.line == 1 for f in scan.findings))

    def test_environment_flags_and_sdk_settings_are_leads_too(self):
        self.source("environment.js", "APP_DISABLE_TELEMETRY=1\nAPP_AUTO_UPDATE=0\nSENTRY_DSN=''\n")
        findings = discovery.scan_sources(self.root).findings
        self.assertIn(("reporting-settings", 1), [(f.rule, f.line) for f in findings])
        self.assertIn(("updates", 2), [(f.rule, f.line) for f in findings])
        self.assertIn(("reporting", 3), [(f.rule, f.line) for f in findings])

    def test_cloud_reporting_routes_and_piggybacked_metadata_are_leads(self):
        cases = (
            ('cloudPost("/api/analytics/events/batch", { events });', "reporting"),
            ('cloudGet("/api/analytics/summary?backfill=true");', "reporting"),
            ('cloudPost("/v2/analytics/participation", enabled);', "reporting"),
            ('cloudDelete("/api/analytics/events/delete", ids);', "reporting"),
            ('request("https://service.invalid/analytics#events");', "reporting"),
            ("const fields = { analyticsOccurredAt, analyticsWordCount };", "reporting"),
            ("payload.analytics_counter_version = 1;", "reporting"),
            ("const fields = { sendLogs: settings.sendLogs };", "reporting-settings"),
            ("settings.setSendLogs(true);", "reporting-settings"),
            ("settings.insightsSyncEnabled = saved;", "reporting-settings"),
            ("settings.setInsightsSyncEnabled(true);", "reporting-settings"),
            ("INSIGHTS_SYNC_ENABLED=1", "reporting-settings"),
            ("settings.analytics_sync_disabled = false;", "reporting-settings"),
            ("headers.sessionId = perLaunchId;", "reporting-settings"),
            ("fields.session_id = requestId;", "reporting-settings"),
            ("sdk.distinctId = saved;", "reporting-settings"),
            ("DISTINCT_ID=fixture", "reporting-settings"),
        )
        self.source("backend/cloud.js", "\n".join(text for text, _ in cases))
        scan = discovery.scan_sources(self.root)
        self.assertFalse(scan.errors)
        leads = {(finding.rule, finding.line) for finding in scan.findings}
        for number, (text, rule) in enumerate(cases, 1):
            with self.subTest(text=text):
                self.assertIn((rule, number), leads)

    def test_exporter_factories_and_custom_callbacks_are_leads_in_both_conditions(self):
        for name in ("index.mjs", "node.mjs"):
            self.source(
                f"node_modules/@vendor/telemetry/dist/{name}",
                "export async function createTelemetry(options, context) {\n"
                "  return { publish: context.customTrack };\n}\n",
            )
        self.source("package-lock.json", '{"dependencies":{"@vendor/telemetry":"1.2.3"}}')
        scan = discovery.scan_sources(self.root)
        self.assertFalse(scan.errors)
        for name in ("index.mjs", "node.mjs"):
            leads = [f for f in scan.findings if f.path.endswith(name)]
            self.assertEqual(
                [(f.rule, f.line) for f in leads], [("reporting", 1), ("reporting", 2)]
            )
        self.assertTrue(
            any(f.kind == "dependency-metadata" and f.rule == "reporting" for f in scan.findings)
        )

    def test_manual_updates_writable_binary_caches_and_remote_components_are_leads(self):
        cases = (
            ("downloadUpdate();", "updates"),
            ("install_update();", "updates"),
            ("updater.quitAndInstall();", "updates"),
            ('spawn(tool, ["--update-to", "nightly"]);', "updates"),
            ('spawn(tool, ["--no-update"]);', "updates"),
            ("downloadBinaries();", "software-installation"),
            ("fetch_binary();", "software-installation"),
            ("seedBinary();", "software-installation"),
            ("ensureExecutable();", "software-installation"),
            ("class GpuBinaryManager {}", "software-installation"),
            ("class BinaryInstaller {}", "software-installation"),
            ('spawn(tool, ["--remote-components", "engine:registry"]);', "software-installation"),
            ('spawn(tool, ["--no-remote-components"]);', "software-installation"),
        )
        self.source("runtime/tools.js", "\n".join(text for text, _ in cases))
        scan = discovery.scan_sources(self.root)
        self.assertFalse(scan.errors)
        leads = {(finding.rule, finding.line) for finding in scan.findings}
        for number, (text, rule) in enumerate(cases, 1):
            with self.subTest(text=text):
                self.assertIn((rule, number), leads)

    def test_reporting_leads_do_not_expand_to_all_network_statistics_or_identifiers(self):
        self.source(
            "ordinary.js",
            "\n".join(
                (
                    "fetch(providerUrl);",
                    'cloudPost("/api/transcribe", { audio, language });',
                    'cloudPost("/api/notes", { text });',
                    'const path = "/api/analytics-assets/icon.svg";',
                    "const analytics = summarizeLocalStatistics();",
                    "const analyticsWordCounter = localWords.length;",
                    "const sessionIdentity = authenticatedUser;",
                    "const distinctIdentifier = selectedRecord;",
                    "const localTokenCount = 10;",
                    "downloadModelData();",
                )
            ),
        )
        scan = discovery.scan_sources(self.root)
        self.assertFalse(scan.errors)
        self.assertFalse(scan.findings)

    def test_reporting_review_questions_preserve_functional_protocols_and_erasure(self):
        self.source("cloud.js", 'cloudDelete("/api/analytics/events/delete", { sessionId });')
        discovery.write_report(discovery.scan_sources(self.root), self.output, "fixture-local")
        report = json.loads((self.output / "discovery.json").read_text())
        self.assertEqual(report["status"], "completed")
        self.assertIn("remote erasure", report["questions"]["reporting"])
        self.assertIn("alongside ordinary requests", report["questions"]["reporting"])
        self.assertIn("authenticate requests", report["questions"]["reporting-settings"])
        self.assertIn("model data", report["questions"]["software-installation"])

    def test_line_numbers_follow_newlines_not_other_unicode_separators(self):
        self.source("entry.js", "// separator\u2028 Sentry.init();\r\ncheckForUpdates();\n")
        findings = discovery.scan_sources(self.root).findings
        self.assertEqual([(f.rule, f.line) for f in findings], [("reporting", 1), ("updates", 2)])

    def test_binary_symlink_and_vcs_exclusions_are_explicit_and_never_followed(self):
        self.source("normal.js", "localStatistics();\n")
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "report.js").write_text("Sentry.init();\n")
        (self.root / "outside-link").symlink_to(outside, target_is_directory=True)
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        (self.root / "broken-link").symlink_to("missing")
        (self.root / "binary").write_bytes(b"\0Sentry")
        self.source(".git/config", "telemetry\n")
        scan = discovery.scan_sources(self.root)
        self.assertFalse(scan.errors)
        self.assertFalse(scan.findings)
        self.assertEqual([file.path for file in scan.files], ["normal.js"])
        self.assertEqual(
            {issue.path for issue in scan.exclusions},
            {"outside-link", "loop", "broken-link", "binary", ".git"},
        )

    def test_unreadable_files_and_directories_save_partial_evidence_then_fail(self):
        self.source("good.js", "checkForUpdates();\n")
        unreadable = self.source("unreadable.js", "telemetry();\n")
        inaccessible = self.root / "inaccessible"
        inaccessible.mkdir()
        original_open = Path.open
        original_iterdir = Path.iterdir

        def open_file(path, *args, **kwargs):
            if path == unreadable:
                raise PermissionError("unreadable fixture")
            return original_open(path, *args, **kwargs)

        def list_directory(path):
            if path == inaccessible:
                raise PermissionError("inaccessible fixture")
            return original_iterdir(path)

        with patch.object(Path, "open", open_file), patch.object(Path, "iterdir", list_directory):
            scan = discovery.scan_sources(self.root)
        self.assertEqual({issue.path for issue in scan.errors}, {"unreadable.js", "inaccessible"})
        with self.assertRaisesRegex(RuntimeError, "Incomplete discovery"):
            discovery.write_report(scan, self.output, "fixture-local")
        report = json.loads((self.output / "discovery.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["findings"][0]["path"], "good.js")
        self.assertIn("unreadable fixture", (self.output / "discovery.txt").read_text())

    def test_non_utf8_large_text_and_special_entries_are_errors_not_successful_skips(self):
        self.source("good.js", "localStatistics();")
        (self.root / "legacy.txt").write_bytes(b"\xffnot UTF-8")
        self.source("large.js", "x" * 33)
        os.mkfifo(self.root / "pipe")
        with patch.object(discovery, "MAX_TEXT_BYTES", 32):
            scan = discovery.scan_sources(self.root)
        self.assertEqual({issue.path for issue in scan.errors}, {"legacy.txt", "large.js", "pipe"})
        self.assertFalse(scan.exclusions)

    def test_missing_empty_and_symlinked_roots_fail(self):
        link = self.root.parent / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        for root in (self.root, self.root / "missing", link):
            with self.subTest(root=root):
                self.assertTrue(discovery.scan_sources(root).errors)

    def test_zero_leads_is_advisory_not_a_compliance_verdict(self):
        self.source("main.js", "fetch(providerUrl);\n")
        discovery.write_report(discovery.scan_sources(self.root), self.output, "fixture-local")
        report = json.loads((self.output / "discovery.json").read_text())
        self.assertEqual(report["status"], "completed")
        self.assertFalse(report["findings"])
        self.assertIn("not patch requirements or a compliance verdict", report["scope"])
        self.assertTrue(report["limitations"])

    def test_reports_are_deterministic_and_escape_untrusted_names_and_evidence(self):
        name = 'odd\x1b[2J"\n.js'
        self.source(name, "x" * 500 + " Sentry.init('\x1b[2J'); " + "y" * 500)
        scan = discovery.scan_sources(self.root)
        self.assertEqual(len(scan.findings), 1)
        self.assertLessEqual(len(scan.findings[0].evidence), 240)
        self.assertIn("Sentry", scan.findings[0].evidence)
        discovery.write_report(scan, self.output, "fixture-local")
        original = (self.output / "discovery.json").read_bytes()
        discovery.write_report(discovery.scan_sources(self.root), self.output, "fixture-local")
        self.assertEqual(original, (self.output / "discovery.json").read_bytes())
        self.assertEqual(asdict(scan), asdict(discovery.scan_sources(self.root)))
        text = (self.output / "discovery.txt").read_text()
        self.assertNotIn("\x1b", text)
        self.assertIn("\\u001b", text)
        self.assertIn("\\n.js", text)
