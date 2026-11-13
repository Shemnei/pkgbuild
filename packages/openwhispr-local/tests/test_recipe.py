"""Fast recipe-local checks; upstream-dependent assertions run in makepkg check()."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("openwhispr_prepare", RECIPE / "prepare.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
DEPLOY_SPEC = importlib.util.spec_from_file_location("openwhispr_deploy", RECIPE / "deploy.py")
DEPLOY = importlib.util.module_from_spec(DEPLOY_SPEC)
DEPLOY_SPEC.loader.exec_module(DEPLOY)


class RecipeTests(unittest.TestCase):
    def test_finalize_preserves_runtime_modules_and_notices_without_node_clis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            modules = stage / "node_modules"
            for name in (
                ".bin",
                "typescript",
                "prebuild-install",
                "flatted/python",
                "better-sqlite3/deps",
                "better-sqlite3/src",
                "ehbp/dist/esm/test",
                "onnxruntime-node/bin/napi-v6/darwin",
                "onnxruntime-node/bin/napi-v6/win32",
                "onnxruntime-node/bin/napi-v6/linux/arm64",
            ):
                (modules / name).mkdir(parents=True)
            (modules / "better-sqlite3/binding.gyp").write_text("build-only definition\n")
            addon = modules / "better-sqlite3/build/Release/better_sqlite3.node"
            addon.parent.mkdir(parents=True)
            addon.write_bytes(b"native runtime addon")
            for name, command in (("separate", "cli.js"), ("dual", "index.js")):
                package = modules / name
                package.mkdir()
                (package / "package.json").write_text(
                    json.dumps(
                        {
                            "name": name,
                            "main": "index.js",
                            "bin": command,
                            "scripts": {"postinstall": "unreviewed installer"},
                        }
                    )
                )
                (package / "index.js").write_text("#!/usr/bin/env node\nmodule.exports = 1;\n")
                (package / "LICENSE").write_text("upstream notice\n")
                (package / "cli.js").write_text("#!/usr/bin/env node\nconsole.log('cli');\n")
            nested = modules / "separate/node_modules/.bin"
            nested.mkdir(parents=True)
            (nested / "removed-cli").symlink_to("../../cli.js")
            licenses = root / "licenses"
            DEPLOY.finalize(stage, licenses)
            self.assertTrue(addon.is_file())
            self.assertFalse((modules / "prebuild-install").exists())
            self.assertFalse((modules / "flatted/python").exists())
            self.assertFalse((modules / "separate/cli.js").exists())
            self.assertFalse(list(modules.rglob(".bin")))
            self.assertFalse(
                [
                    directory
                    for directory in modules.rglob("*")
                    if directory.is_dir() and not any(directory.iterdir())
                ]
            )
            for name in ("separate", "dual"):
                file = modules / name / "index.js"
                self.assertEqual(file.read_text(), "module.exports = 1;\n")
                self.assertEqual(file.stat().st_mode & 0o777, 0o644)
                self.assertEqual((licenses / name / "LICENSE").read_text(), "upstream notice\n")
                manifest = json.loads((modules / name / "package.json").read_text())
                self.assertNotIn("bin", manifest)
                self.assertNotIn("scripts", manifest)

    def test_policy_entry_points(self):
        subprocess.run(["node", "--test", str(RECIPE / "policy.test.mjs")], check=True)

    def test_drift_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in PREPARE.INPUTS:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("unreviewed source\n")
            with self.assertRaisesRegex(ValueError, "Unaudited upstream input"):
                PREPARE.prepare(root)
            for name in PREPARE.INPUTS:
                self.assertEqual((root / name).read_text(), "unreviewed source\n")

    def test_replacement_counts_fail_closed(self):
        with self.assertRaises(ValueError):
            PREPARE.replace("two two", "two", "one")
        with self.assertRaises(ValueError):
            PREPARE.region("end start", "start", "end", "replacement")

    def test_desktop_metadata(self):
        subprocess.run(["desktop-file-validate", str(RECIPE / "open-whispr.desktop")], check=True)

    def test_runtime_manifest_projection_preserves_locked_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, target = Path(temporary) / "source", Path(temporary) / "stage"
            for name in DEPLOY.DIRECTORIES:
                (root / name).mkdir(parents=True)
            for name in DEPLOY.FILES:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("fixture\n")
            dependencies = {
                "dotenv": "1.2.3",
                "shadcn-ui": "0.9.5",
                "electron-updater": "6.6.2",
                "ffmpeg-static": "5.2.0",
            }
            package = {"dependencies": dependencies, "scripts": {"postinstall": "forbidden"}}
            locked = {"version": "1.2.3", "integrity": "unchanged fixture"}
            lock = {"packages": {"": package, "node_modules/dotenv": locked}}
            (root / "package.json").write_text(json.dumps(package))
            (root / "package-lock.json").write_text(json.dumps(lock))
            DEPLOY.deploy(root, target)
            staged = json.loads((target / "package.json").read_text())
            staged_lock = json.loads((target / "package-lock.json").read_text())
            self.assertNotIn("scripts", staged)
            self.assertEqual(staged["dependencies"], {"dotenv": "1.2.3"})
            self.assertEqual(staged_lock["packages"][""]["dependencies"], staged["dependencies"])
            self.assertEqual(staged_lock["packages"]["node_modules/dotenv"], locked)

    def test_deploy_refuses_to_overwrite_existing_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "marker"
            marker.write_text("preserve")
            with self.assertRaises(FileExistsError):
                DEPLOY.deploy(root / "unused", root)
            self.assertEqual(marker.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
