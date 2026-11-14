"""Offline contracts for the standalone recipe and atomic upstream drift checks."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RECIPE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("openspec_prepare", RECIPE / "prepare.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
DEPLOY_SPEC = importlib.util.spec_from_file_location("openspec_deploy", RECIPE / "deploy.py")
DEPLOY = importlib.util.module_from_spec(DEPLOY_SPEC)
DEPLOY_SPEC.loader.exec_module(DEPLOY)


class RecipeTests(unittest.TestCase):
    def test_deploy_preserves_versions_cycles_and_optional_peers_without_dev_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            for directory in ("dist", "bin", "schemas"):
                (source / directory).mkdir(parents=True)
            for name in ("README.md", "LICENSE"):
                (source / name).write_text(name)
            (source / "package.json").write_text(
                json.dumps(
                    {
                        "name": "example",
                        "dependencies": {"first": "1"},
                        "devDependencies": {"dev-tool": "1"},
                        "scripts": {"prepare": "unreviewed"},
                    }
                )
            )
            modules = source / "node_modules"
            for name, metadata in {
                "first": {"version": "1", "dependencies": {"second": "2"}},
                "second": {
                    "version": "2",
                    "dependencies": {"first": "1"},
                    "peerDependencies": {"absent": "1", "@types/node": "22"},
                    "peerDependenciesMeta": {
                        "absent": {"optional": True},
                        "@types/node": {"optional": True},
                    },
                },
                "dev-tool": {"version": "1"},
                "@types/node": {"version": "22"},
            }.items():
                (modules / name).mkdir(parents=True)
                (modules / name / "package.json").write_text(json.dumps(metadata))
            destination = root / "staged"
            DEPLOY.deploy(source, destination)
            first = destination / "node_modules/first"
            second = first / "node_modules/second"
            self.assertEqual(json.loads((second / "package.json").read_text())["version"], "2")
            self.assertEqual((second / "node_modules/first").resolve(), first.resolve())
            self.assertFalse((destination / "node_modules/dev-tool").exists())
            self.assertFalse((second / "node_modules/@types/node").exists())
            manifest = json.loads((destination / "package.json").read_text())
            self.assertNotIn("scripts", manifest)
            self.assertNotIn("devDependencies", manifest)
            for link in destination.rglob("*"):
                if link.is_symlink():
                    self.assertTrue(link.resolve().is_relative_to(destination))
            with self.assertRaisesRegex(ValueError, "overwrite"):
                DEPLOY.deploy(source, destination)

    def test_deploy_rejects_dependency_path_traversal(self):
        for name in ("../escape", "@scope/../escape", "/absolute", ""):
            with self.assertRaisesRegex(ValueError, "Invalid dependency"):
                DEPLOY.resolve_package(RECIPE, name)

    def test_deploy_fails_on_missing_dependencies_and_external_links(self):
        for external in (False, True):
            with self.subTest(external=external), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source"
                for directory in ("dist", "bin", "schemas", "node_modules"):
                    (source / directory).mkdir(parents=True)
                for name in ("LICENSE", "README.md"):
                    (source / name).write_text(name)
                dependency = "openspec-required-test-dependency"
                (source / "package.json").write_text(
                    json.dumps({"dependencies": {dependency: "1"}})
                )
                if external:
                    outside = root / "external"
                    outside.mkdir()
                    (outside / "package.json").write_text("{}")
                    (source / "node_modules" / dependency).symlink_to(outside)
                with self.assertRaisesRegex(ValueError, "escaped|Missing"):
                    DEPLOY.deploy(source, root / "destination")

    def test_prepare_replaces_only_reviewed_boundaries_and_manager_field(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                '{"packageManager":"pnpm@10.34.5","dependencies":{"example":"1"}}'
            )
            (root / "input.ts").write_text("original")
            with (
                patch.object(
                    PREPARE,
                    "BOUNDARIES",
                    {"input.ts": (hashlib.sha256(b"original").hexdigest(), "telemetry.ts")},
                ),
                patch.object(PREPARE, "TRANSFORMS", {}),
            ):
                PREPARE.prepare(root, RECIPE)
            self.assertEqual(
                (root / "input.ts").read_bytes(), (RECIPE / "telemetry.ts").read_bytes()
            )
            self.assertEqual(
                json.loads((root / "package.json").read_text()),
                {"dependencies": {"example": "1"}},
            )

    def test_drift_rejects_every_edit_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text('{"packageManager":"pnpm@10.34.5"}')
            manifest = (root / "package.json").read_bytes()
            good = root / "good.ts"
            bad = root / "bad.ts"
            good.write_text("original")
            bad.write_text("drift")
            boundaries = {
                "good.ts": (hashlib.sha256(b"original").hexdigest(), "telemetry.ts"),
                "bad.ts": (hashlib.sha256(b"expected").hexdigest(), "telemetry.ts"),
            }
            with patch.object(PREPARE, "BOUNDARIES", boundaries):
                with self.assertRaisesRegex(ValueError, "boundary changed"):
                    PREPARE.prepare(root, RECIPE)
            self.assertEqual(good.read_text(), "original")
            self.assertEqual((root / "package.json").read_bytes(), manifest)

    def test_unknown_package_manager_pin_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text('{"packageManager":"pnpm@99.0.0"}')
            with self.assertRaisesRegex(ValueError, "manager pin changed"):
                PREPARE.prepare(root, RECIPE)

    def test_transform_drift_is_atomic_and_duplicate_matches_fail(self):
        for text in ("changed", "expected expected"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "package.json").write_text('{"packageManager":"pnpm@10.34.5"}')
                manifest = (root / "package.json").read_bytes()
                (root / "input.ts").write_text(text)
                with (
                    patch.object(PREPARE, "BOUNDARIES", {}),
                    patch.object(
                        PREPARE,
                        "TRANSFORMS",
                        {
                            "input.ts": (
                                hashlib.sha256(text.encode()).hexdigest(),
                                [("expected", "replacement")],
                            )
                        },
                    ),
                    self.assertRaisesRegex(ValueError, "not unique"),
                ):
                    PREPARE.prepare(root, RECIPE)
                self.assertEqual((root / "input.ts").read_text(), text)
                self.assertEqual((root / "package.json").read_bytes(), manifest)

    def test_recipe_keeps_build_work_out_of_package(self):
        recipe = (RECIPE / "PKGBUILD").read_text()
        package = recipe.split("package() {", 1)[1]
        self.assertNotIn("pnpm", package)
        self.assertNotIn("node ", package)
        self.assertIn("--frozen-lockfile --ignore-scripts", recipe)
        self.assertIn('python "$srcdir/deploy.py"', recipe)
        self.assertIn("completion generate", recipe.split("check()", 1)[0])
        self.assertIn("arch=('any')", recipe)

    def test_policy_replacements_have_no_reporting_or_process_implementation(self):
        for name in ("telemetry.ts", "version-check.ts", "install-standalone.ts"):
            source = (RECIPE / name).read_text()
            for forbidden in ("fetch(", "spawn(", "randomUUID", "https://"):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
