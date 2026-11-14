import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ci
from support.ci import changed_paths, selection
from support.containers.config import snapshot


class SelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.recipe("provider-local")
        self.recipe("tool-local", build=["provider-local"])
        self.recipe("consumer-local", runtime=["tool-local"])
        self.recipe("unrelated-local")

    def recipe(self, name, *, runtime=(), build=()):
        directory = self.root / "packages" / name
        directory.mkdir(parents=True)
        (directory / "PKGBUILD").write_text(f"pkgname={name}\n")
        (directory / "validation.toml").write_text(
            f"local_dependencies = {json.dumps(list(runtime))}\n"
            f"local_build_dependencies = {json.dumps(list(build))}\n"
        )

    def test_provider_changes_include_transitive_runtime_and_build_consumers(self):
        result = selection(self.root, ["packages/provider-local/input.patch"])
        self.assertEqual(result["packages"], ["consumer-local", "provider-local", "tool-local"])
        self.assertFalse(result["framework"])

    def test_shared_changes_and_manual_runs_select_everything_and_framework(self):
        for paths in (None, ["support/ci.py"], [".github/workflows/example.yml"], ["README.md"]):
            with self.subTest(paths=paths):
                result = selection(self.root, paths)
                self.assertEqual(len(result["packages"]), 4)
                self.assertTrue(result["framework"])

    def test_leaf_changes_empty_diffs_and_deleted_recipes(self):
        self.assertEqual(
            selection(self.root, ["packages/consumer-local/tests/test_recipe.py"]),
            {"packages": ["consumer-local"], "framework": False},
        )
        for paths in ([], ["packages/deleted-local/PKGBUILD"]):
            self.assertEqual(selection(self.root, paths), {"packages": [], "framework": False})

    def test_diff_handles_merge_base_null_paths_and_renames(self):
        base, head = "a" * 40, "b" * 40
        with patch("support.ci.subprocess.run") as run:
            run.return_value.stdout = b"packages/old-local/PKGBUILD\0packages/new-local/a b\0"
            paths = changed_paths(self.root, base, head, pull_request=True)
        self.assertEqual(paths, ["packages/old-local/PKGBUILD", "packages/new-local/a b"])
        self.assertEqual(
            run.call_args.args[0],
            ["git", "diff", "--no-renames", "--name-only", "-z", f"{base}...{head}", "--"],
        )
        with patch("support.ci.subprocess.run") as run:
            run.return_value.stdout = b""
            changed_paths(self.root, base, head)
            self.assertIn(f"{base}..{head}", run.call_args.args[0])

    def test_first_push_selects_all_and_invalid_or_missing_refs_fail(self):
        self.assertIsNone(changed_paths(self.root, "0" * 40, "a" * 40))
        for ref in ("--help", "main", "", "a" * 39):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                changed_paths(self.root, ref, "a" * 40)
        with (
            patch("support.ci.subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            changed_paths(self.root, "a" * 40, "b" * 40)

    def test_cli_emits_single_line_json_outputs_for_github(self):
        output = self.root / "outputs"
        with (
            patch.object(ci, "ROOT", self.root),
            patch("sys.argv", ["support.ci"]),
            patch.dict("os.environ", {"GITHUB_OUTPUT": str(output)}),
        ):
            ci.main()
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(json.loads(values["packages"]), selection(self.root)["packages"])
        self.assertIs(json.loads(values["framework"]), True)

    def test_real_git_diff_limits_pr_changes_to_the_feature_branch(self):
        def git(*args):
            return subprocess.run(
                ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
            ).stdout.strip()

        git("init", "--initial-branch=main")
        git("config", "user.name", "CI fixture")
        git("config", "user.email", "ci@example.invalid")
        git("add", ".")
        git("commit", "-m", "initial")
        initial = git("rev-parse", "HEAD")
        git("checkout", "-b", "feature")
        (self.root / "packages" / "consumer-local" / "change.patch").write_text("fixture\n")
        git("add", ".")
        git("commit", "-m", "feature")
        head = git("rev-parse", "HEAD")
        git("checkout", "main")
        (self.root / "README.md").write_text("unrelated base-branch change\n")
        git("add", ".")
        git("commit", "-m", "base moved")
        base = git("rev-parse", "HEAD")
        self.assertEqual(
            changed_paths(self.root, base, head, pull_request=True),
            ["packages/consumer-local/change.patch"],
        )
        self.assertEqual(changed_paths(self.root, initial, base), ["README.md"])


class WorkflowContracts(unittest.TestCase):
    def test_workflows_are_copied_into_validation_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            manifest = snapshot(root, Path(temporary))
        for name in ("validation", "upstream"):
            self.assertIn(f".github/workflows/{name}.yml", manifest)

    def test_workflows_keep_credentials_and_publication_scoped(self):
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for path in root.glob("*.yml"):
            text = path.read_text()
            self.assertNotIn("pull_request_target", text)
            self.assertNotIn("secrets.", text)
            self.assertNotIn("persist-credentials: true", text)
            self.assertNotIn("contents: write", text)
            for line in text.splitlines():
                if "uses:" in line:
                    self.assertRegex(line, r"uses: [\w/-]+@[a-f0-9]{40} #")
        validation = (root / "validation.yml").read_text()
        self.assertNotIn("issues: write", validation)
        self.assertNotIn("schedule:", validation)
        self.assertIn("name: CI result", validation)
        watcher = (root / "upstream.yml").read_text()
        self.assertIn("issues: write", watcher)
        self.assertIn("github.event.repository.default_branch", watcher)
        self.assertIn("cancel-in-progress: false", watcher)
