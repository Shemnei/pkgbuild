import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from support.upstream import GitHub, NoRedirect, upstream, version, watch


def release(tag="v2.0.0", **changes):
    return {"tag_name": tag, "draft": False, "prerelease": False} | changes


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "packages" / "example-local"
        self.directory.mkdir(parents=True)
        # Executing this recipe would fail; the watcher reads metadata only.
        (self.directory / "PKGBUILD").write_text("exit 99\n")
        (self.directory / ".SRCINFO").write_text(
            "pkgname = example-local\npkgver = 1.0.0\nurl = https://github.com/vendor/project\n"
        )
        self.github = Mock()
        self.github.issues.return_value = []
        self.github.request.side_effect = [release(), {"html_url": "https://example.invalid/1"}]

    def test_numeric_stable_version_comparison_and_tag_validation(self):
        self.assertGreater(version("v1.10.0"), version("1.9.9"))
        self.assertEqual(version("v2.0.0+build.1"), version("2"))
        for value in ("nightly", "v2.0.0-rc.1", "1_2", "1.2\n", "../../1", "v1 foo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version(value)

    def test_issue_contains_assignment_release_link_and_update_checklist(self):
        watch(self.root, self.github, "owner/recipes", "maintainer")
        path, issue = self.github.request.call_args.args
        self.assertEqual(path, "/repos/owner/recipes/issues")
        self.assertEqual(issue["assignees"], ["maintainer"])
        self.assertEqual(issue["title"], "Update example-local to v2.0.0")
        self.assertIn("<!-- upstream-release:example-local:2 -->", issue["body"])
        self.assertIn("https://github.com/vendor/project/releases/tag/v2.0.0", issue["body"])
        self.assertIn("Packaged version: `1.0.0`", issue["body"])
        self.assertIn("clean-chroot", issue["body"])
        self.assertIn("updater/reporting", issue["body"])

    def test_open_closed_and_null_issue_bodies_are_handled_without_duplicates(self):
        for state in ("open", "closed"):
            self.github.reset_mock()
            self.github.issues.return_value = [
                {"body": None},
                {"body": "<!-- upstream-release:example-local:2 -->", "state": state},
            ]
            self.github.request.side_effect = [release()]
            watch(self.root, self.github, "owner/recipes", "maintainer")
            self.assertEqual(self.github.request.call_count, 1)

    def test_equal_older_and_dry_runs_never_create_issues(self):
        for tag, dry_run in (("v1.0.0", False), ("v0.9.0", False), ("v2.0.0", True)):
            with self.subTest(tag=tag, dry_run=dry_run):
                self.github.reset_mock()
                self.github.request.side_effect = [release(tag)]
                watch(self.root, self.github, "owner/recipes", "maintainer", dry_run=dry_run)
                self.assertEqual(self.github.request.call_count, 1)

    def test_unstable_releases_and_api_failures_are_errors_not_successful_skips(self):
        for response in (
            release(prerelease=True),
            release(draft=True),
            release("v2.0.0-beta"),
            {},
            urllib.error.HTTPError("https://api.github.com", 403, "rate limit", {}, None),
        ):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                self.github.request.side_effect = [response]
                watch(self.root, self.github, "owner/recipes", "maintainer")

    def test_one_upstream_failure_does_not_hide_other_package_notifications(self):
        second = self.root / "packages" / "second-local"
        second.mkdir()
        (second / "PKGBUILD").write_text("exit 99\n")
        (second / ".SRCINFO").write_text(
            (self.directory / ".SRCINFO").read_text().replace("example-local", "second-local")
        )
        self.github.request.side_effect = [
            OSError("network failed"),
            release(),
            {"html_url": "https://example.invalid/2"},
        ]
        with self.assertRaisesRegex(RuntimeError, "example-local: network failed"):
            watch(self.root, self.github, "owner/recipes", "maintainer")
        self.assertEqual(
            self.github.request.call_args.args[1]["title"], "Update second-local to v2.0.0"
        )

    def test_metadata_and_destination_validation_fail_closed(self):
        for url in ("https://example.invalid", "https://github.com/owner/repo/extra"):
            (self.directory / ".SRCINFO").write_text(
                f"pkgname = example-local\npkgver = 1\nurl = {url}\n"
            )
            with self.assertRaises(ValueError):
                upstream(self.directory)
        for repository, assignee in (("../repos", "maintainer"), ("owner/repo", "@someone")):
            with self.assertRaises(ValueError):
                watch(self.root, self.github, repository, assignee)


class GitHubTests(unittest.TestCase):
    def test_issue_pagination_includes_closed_issues_and_ignores_pull_requests(self):
        github = GitHub("test-token")
        first = [{"body": str(i)} for i in range(99)] + [{"pull_request": {}, "body": "PR"}]
        with patch.object(github, "request", side_effect=[first, [{"body": "closed"}]]):
            issues = list(github.issues("owner/recipes"))
            self.assertEqual(len(issues), 100)
            self.assertEqual(issues[-1]["body"], "closed")
            self.assertIn("state=all&per_page=100&page=2", github.request.call_args.args[0])

    def test_client_uses_fixed_api_origin_timeout_and_json_post(self):
        github = GitHub("test-token")
        response = io.BytesIO(b'{"number": 1}')
        with patch.object(github.opener, "open", return_value=response) as opened:
            self.assertEqual(
                github.request("/repos/owner/repo/issues", {"title": "update"}), {"number": 1}
            )
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/owner/repo/issues")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"title": "update"})
        self.assertEqual(opened.call_args.kwargs["timeout"], 60)

    def test_redirects_never_forward_the_token_to_another_origin(self):
        with self.assertRaisesRegex(ValueError, "redirected"):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid")
