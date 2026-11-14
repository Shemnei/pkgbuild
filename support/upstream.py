"""Watch stable GitHub releases using committed metadata, never PKGBUILD execution."""

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from support.recipes import recipes, srcinfo

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.{1,2}$)[A-Za-z0-9_.-]+")
VERSION = re.compile(r"v?(\d+(?:\.\d+)*)(?:\+[A-Za-z0-9.-]+)?")


def version(value):
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError(f"Unsupported stable release version: {value!r}")
    parts = [int(part) for part in match[1].split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirected; update the repository metadata")


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, data=None):
        request = urllib.request.Request(
            f"https://api.github.com{path}",
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pkgbuild-upstream-watcher",
            },
        )
        with self.opener.open(request, timeout=60) as response:
            return json.load(response)

    def issues(self, repository):
        page = 1
        while True:
            batch = self.request(f"/repos/{repository}/issues?state=all&per_page=100&page={page}")
            yield from (issue for issue in batch if "pull_request" not in issue)
            if len(batch) < 100:
                return
            page += 1


def upstream(directory):
    fields = srcinfo((directory / ".SRCINFO").read_text())
    if fields.get("pkgname") != [directory.name] or len(fields.get("pkgver", [])) != 1:
        raise ValueError(f"Invalid release metadata in {directory}")
    urls = fields.get("url", [])
    if len(urls) != 1 or not urls[0].startswith("https://github.com/"):
        raise ValueError(f"Expected a GitHub repository URL in {directory}/.SRCINFO")
    repository = urls[0].removeprefix("https://github.com/").removesuffix("/")
    if not REPOSITORY.fullmatch(repository):
        raise ValueError(f"Invalid upstream GitHub repository: {repository!r}")
    current = fields["pkgver"][0]
    version(current)
    return repository, current


def watch(root, github, repository, assignee, *, dry_run=False):
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid destination GitHub repository")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", assignee):
        raise ValueError("Invalid issue assignee")
    issues = list(github.issues(repository))
    failures = []
    for name, directory in recipes(root).items():
        try:
            source, current = upstream(directory)
            release = github.request(f"/repos/{source}/releases/latest")
            if release["draft"] or release["prerelease"]:
                raise ValueError("Latest release is not stable")
            tag = release["tag_name"]
            latest = version(tag)
            if latest <= version(current):
                print(f"{name}: packaged {current}; no newer stable release")
                continue
            normalized = ".".join(map(str, latest))
            marker = f"<!-- upstream-release:{name}:{normalized} -->"
            if any(marker in (issue.get("body") or "") for issue in issues):
                print(f"{name}: {tag} already reported (open or closed)")
                continue
            # Construct the link from validated data, not arbitrary API markdown.
            link = f"https://github.com/{source}/releases/tag/{tag}"
            body = (
                f"{marker}\n"
                f"Upstream has a newer stable release for `{name}`.\n\n"
                f"- Packaged version: `{current}`\n"
                f"- Upstream version: `{tag}`\n"
                f"- Release: {link}\n\n"
                "## Update checklist\n\n"
                "- [ ] Review upstream release notes and dependency/lockfile changes.\n"
                "- [ ] Update pinned sources and strong checksums.\n"
                "- [ ] Audit updater/reporting boundaries and supported integrations; "
                "refresh downstream changes and regression tests.\n"
                "- [ ] Regenerate `.SRCINFO` and update durable recipe documentation.\n"
                "- [ ] Run container check, source verification, and installed-package tests.\n"
                "- [ ] Complete genuine clean-chroot and recipe-specific release gates.\n\n"
                "This is a release notification, not an implemented or validated update.\n"
            )
            if dry_run:
                print(f"Would open issue for {name}: {current} -> {tag} ({link})")
            else:
                issue = github.request(
                    f"/repos/{repository}/issues",
                    {"title": f"Update {name} to {tag}", "body": body, "assignees": [assignee]},
                )
                issues.append(issue)
                print(f"Created {issue['html_url']}")
        except (KeyError, ValueError, OSError) as error:
            # A broken upstream must not hide notifications for other packages.
            failures.append(f"{name}: {error}")
    if failures:
        raise RuntimeError("Upstream checks failed:\n" + "\n".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    watch(
        ROOT,
        GitHub(os.environ["GH_TOKEN"]),
        os.environ["GITHUB_REPOSITORY"],
        os.environ["UPSTREAM_ISSUE_ASSIGNEE"],
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
