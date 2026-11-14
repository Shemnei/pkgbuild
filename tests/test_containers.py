import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import recipes
from support.artifacts import write_manifest
from support.containers import __main__ as workflow
from support.containers import config, engine, worker


class ContainerFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in config.ROOT_FILES:
            (self.root / name).write_text("fixture\n")
        for name in ("support", "tests", "packages"):
            (self.root / name).mkdir()
        self.recipe("example-local")

    def recipe(self, name, dependencies=()):
        directory = self.root / "packages" / name
        directory.mkdir(exist_ok=True)
        (directory / "PKGBUILD").write_text(f"pkgname={name}\n")
        (directory / ".SRCINFO").write_text(f"pkgname = {name}\nsource = input.patch\n")
        (directory / "input.patch").write_text("reviewed local source\n")
        (directory / "tests").mkdir(exist_ok=True)
        (directory / "tests" / "installed.py").write_text("print('installed test')\n")
        (directory / "validation.toml").write_text(
            f"local_dependencies = {json.dumps(list(dependencies))}\n"
        )
        return directory


class ContainerConfigTests(ContainerFixture):
    def test_build_environment_excludes_transitive_build_only_tools(self):
        self.recipe("generator-local")
        library = self.recipe("library-local")
        (library / "validation.toml").write_text('local_build_dependencies = ["generator-local"]\n')
        self.recipe("example-local", ["library-local"])
        self.assertEqual(
            recipes.build_order(self.root, "example-local"),
            ["generator-local", "library-local", "example-local"],
        )
        self.assertEqual(recipes.build_installation(self.root, "example-local"), ["library-local"])
        self.assertEqual(
            recipes.build_installation(self.root, "library-local"), ["generator-local"]
        )

    def test_snapshot_includes_install_and_changelog_without_source_entries(self):
        directory = self.root / "packages" / "example-local"
        with (directory / ".SRCINFO").open("a") as metadata:
            metadata.write("install = example.install\nchangelog = history.txt\n")
        for name in ("example.install", "history.txt"):
            (directory / name).write_text("declared input\n")
        manifest = config.snapshot(self.root, self.root / "snapshot")
        for name in ("example.install", "history.txt"):
            self.assertIn(f"packages/example-local/{name}", manifest)

    def test_snapshot_checks_paths_for_every_declared_input(self):
        directory = self.root / "packages" / "example-local"
        for key in ("install", "changelog"):
            (directory / ".SRCINFO").write_text(f"{key} = ../../outside\n")
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "escapes"):
                config.snapshot(self.root, self.root / "snapshot")

    def test_scoped_snapshot_ignores_unfinished_unrelated_recipes(self):
        unfinished = self.root / "packages" / "unfinished-local"
        unfinished.mkdir()
        (unfinished / "PKGBUILD").write_text("unfinished\n")
        manifest = config.snapshot(self.root, self.root / "snapshot", ["example-local"])
        self.assertFalse(any("unfinished-local" in name for name in manifest))

    def test_metadata_snapshot_bootstraps_without_old_sources_or_srcinfo(self):
        directory = self.root / "packages" / "example-local"
        (directory / ".SRCINFO").unlink()
        manifest = config.snapshot(
            self.root, self.root / "snapshot", ["example-local"], include_sources=False
        )
        self.assertIn("packages/example-local/PKGBUILD", manifest)
        self.assertNotIn("packages/example-local/input.patch", manifest)

    def test_dependency_graph_is_ordered_deduplicated_and_recipe_local(self):
        self.recipe("shared-local")
        self.recipe("first-local", ["shared-local"])
        self.recipe("second-local", ["shared-local"])
        self.recipe("example-local", ["first-local", "second-local"])
        self.assertEqual(
            recipes.build_order(self.root, "example-local"),
            ["shared-local", "first-local", "second-local", "example-local"],
        )

    def test_dependency_graph_rejects_cycles_missing_recipes_and_unsafe_names(self):
        self.recipe("example-local", ["missing-local"])
        with self.assertRaisesRegex(ValueError, "Unknown local recipe"):
            recipes.build_order(self.root, "example-local")
        self.recipe("missing-local", ["example-local"])
        with self.assertRaisesRegex(ValueError, "cycle"):
            recipes.build_order(self.root, "example-local")
        with self.assertRaisesRegex(ValueError, "Unknown local recipe"):
            recipes.build_order(self.root, "../example-local")

    def test_validation_manifest_is_strict_and_optional(self):
        directory = self.root / "packages" / "example-local"
        manifest = directory / "validation.toml"
        manifest.unlink()
        self.assertEqual(
            recipes.recipe_config(directory),
            recipes.Validation(),
        )
        for text in (
            "unknown = []",
            'local_dependencies = "other-local"',
            'local_dependencies = ["../other-local"]',
            'local_dependencies = ["other-local", "other-local"]',
            'test_dependencies = ["--root=/host"]',
            'local_dependencies = ["other-local"]\nlocal_build_dependencies = ["other-local"]',
        ):
            manifest.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                recipes.recipe_config(directory)

    def test_build_only_local_dependencies_do_not_enter_the_runtime_closure(self):
        self.recipe("tool-local")
        self.recipe("runtime-local")
        directory = self.recipe("example-local", ["runtime-local"])
        with (directory / "validation.toml").open("a") as manifest:
            manifest.write('local_build_dependencies = ["tool-local"]\n')
        self.assertEqual(
            recipes.build_order(self.root, "example-local"),
            ["runtime-local", "tool-local", "example-local"],
        )
        self.assertEqual(
            recipes.runtime_closure(self.root, "example-local"),
            ["runtime-local", "example-local"],
        )

    def test_image_lock_requires_a_digest_platform_and_snapshot_date(self):
        path = self.root / "image.lock.json"
        original = config.image_lock(workflow.CONTEXT / "image.lock.json")
        for key, value in (
            ("base_image", "archlinux:latest"),
            ("platform", "linux/arm64"),
            ("arch_snapshot", "2026-99-01"),
        ):
            path.write_text(json.dumps({**original, key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError):
                config.image_lock(path)

    def test_snapshot_copies_only_declared_sources_and_generic_framework_inputs(self):
        directory = self.root / "packages" / "example-local"
        for name in ("src", "pkg"):
            (directory / name).mkdir()
            (directory / name / "secret").write_text("not an input")
        (directory / "remote.tar.gz").write_text("download cache")
        (directory / "local.tar.gz").write_text("declared archive")
        (directory / ".SRCINFO").write_text(
            "source = input.patch\nsource = remote.tar.gz::https://example.invalid/release\n"
            "source_x86_64 = local.tar.gz\n"
        )
        (self.root / ".env").write_text("not a repository input")
        (directory / "namcap.toml").write_text('pkgname = "example-local"\n')
        destination = self.root / "snapshot"
        manifest = config.snapshot(self.root, destination)
        self.assertNotIn(".env", manifest)
        self.assertNotIn("packages/example-local/remote.tar.gz", manifest)
        self.assertFalse(any("secret" in name for name in manifest))
        self.assertEqual(
            manifest["packages/example-local/local.tar.gz"],
            hashlib.sha256(b"declared archive").hexdigest(),
        )
        self.assertTrue((destination / "packages/example-local/tests/installed.py").is_file())
        self.assertIn("packages/example-local/namcap.toml", manifest)

    def test_snapshot_rejects_traversal_and_symlinked_sources(self):
        directory = self.root / "packages" / "example-local"
        for source in ("../../.env", "/etc/passwd", "."):
            (directory / ".SRCINFO").write_text(f"source = {source}\n")
            with self.subTest(source=source), self.assertRaises(ValueError):
                config.snapshot(self.root, self.root / "snapshot")
        (directory / ".SRCINFO").write_text("source = input.patch\n")
        (directory / "input.patch").unlink()
        (directory / "input.patch").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "Symlinked"):
            config.snapshot(self.root, self.root / "snapshot")

    def test_symlinked_recipe_directories_are_not_followed(self):
        (self.root / "packages/alias-local").symlink_to(self.root / "packages/example-local")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            recipes.build_order(self.root, "alias-local")


class ContainerEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = object.__new__(engine.Engine)
        self.engine.name = "docker"

    def test_commands_preserve_sandbox_and_never_bind_mount_the_host(self):
        for phase in (
            "check",
            "metadata-inputs",
            "srcinfo",
            "verify",
            "discover",
            "build",
            "lint",
            "install",
            "smoke",
        ):
            arguments = self.engine.create_arguments(
                "image-id", phase, "example-local", "linux/amd64"
            )
            with self.subTest(phase=phase):
                self.assertIn("no-new-privileges", arguments)
                self.assertNotIn("--privileged", arguments)
                self.assertNotIn("--volume", arguments)
                self.assertNotIn("--mount", arguments)
                self.assertNotIn("--cap-add", arguments)
                self.assertEqual(
                    "--network" in arguments,
                    phase in {"check", "metadata-inputs", "srcinfo", "lint", "smoke"},
                )
                if "--network" in arguments:
                    self.assertEqual(arguments[arguments.index("--network") + 1], "none")

    def test_rootless_requirement_is_enforced_without_sudo_or_engine_fallback(self):
        with (
            patch.object(engine.shutil, "which", return_value="/usr/bin/docker"),
            patch.object(engine, "command", return_value='["name=seccomp"]') as command,
            self.assertRaisesRegex(ValueError, "not rootless"),
        ):
            engine.Engine("docker", require_rootless=True)
        command.assert_called_once()

    def test_both_engine_rootless_status_formats_are_supported(self):
        for name, response in (("docker", '["name=rootless"]'), ("podman", "true\n")):
            with (
                self.subTest(engine=name),
                patch.object(engine.shutil, "which", return_value=f"/usr/bin/{name}"),
                patch.object(engine, "command", return_value=response),
            ):
                self.assertTrue(engine.Engine(name, require_rootless=True).rootless)

    def test_image_build_uses_each_engines_explicit_pull_policy(self):
        lock = config.image_lock(workflow.CONTEXT / "image.lock.json")
        for name, policy in (("docker", "--pull"), ("podman", "--pull=always")):
            self.engine.name = name
            with (
                self.subTest(engine=name),
                tempfile.TemporaryDirectory() as temporary,
                patch.object(engine.subprocess, "run", return_value=SimpleNamespace(returncode=1)),
                patch.object(engine, "logged", return_value=0) as logged,
                patch.object(engine, "command", return_value="sha256:fixture"),
            ):
                image = self.engine.image(workflow.CONTEXT, lock, "tools", Path(temporary))
            self.assertEqual(image["id"], "sha256:fixture")
            self.assertIn(policy, logged.call_args.args[0])

    def test_created_container_is_removed_when_copy_or_execution_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            calls = []

            def command(*arguments, **kwargs):
                calls.append(arguments)
                if arguments[1] == "create":
                    return "a" * 64
                if arguments[1] == "cp":
                    raise RuntimeError("copy failed")
                return ""

            with (
                patch.object(engine, "command", side_effect=command),
                self.assertRaises(RuntimeError),
            ):
                self.engine.job(
                    "image", "check", None, "linux/amd64", directory, {}, directory / "job"
                )
            self.assertEqual(calls[-1], ("docker", "rm", "--force", "a" * 64))

    def test_failed_artifact_export_preserves_the_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def command(*arguments, **kwargs):
                if arguments[1] == "create":
                    return "b" * 64
                if arguments[1] == "cp" and ":/output/" in str(arguments[2]):
                    raise subprocess.CalledProcessError(1, arguments)
                return ""

            with (
                patch.object(engine, "command", side_effect=command) as transport,
                patch.object(engine, "logged", return_value=1),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                self.engine.job(
                    "image",
                    "install",
                    "example-local",
                    "linux/amd64",
                    directory,
                    {},
                    directory / "job",
                    commit="owned-runtime",
                )
            self.assertFalse(any(call.args[1] == "rm" for call in transport.call_args_list))
            self.assertFalse(any(call.args[1] == "commit" for call in transport.call_args_list))

    def test_successful_install_exports_before_committing_and_removes_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (
                patch.object(engine, "command", return_value="c" * 64) as transport,
                patch.object(engine, "logged", return_value=0),
            ):
                self.engine.job(
                    "image",
                    "install",
                    "example-local",
                    "linux/amd64",
                    directory,
                    {},
                    directory / "job",
                    commit="owned-runtime",
                )
            operations = [call.args[1] for call in transport.call_args_list]
            self.assertEqual(operations, ["create", "cp", "cp", "commit", "rm"])

    def test_execution_exception_is_not_masked_by_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (
                patch.object(engine, "command", return_value="d" * 64) as transport,
                patch.object(engine, "logged", side_effect=RuntimeError("execution interrupted")),
                self.assertRaisesRegex(RuntimeError, "execution interrupted"),
            ):
                self.engine.job(
                    "image", "check", None, "linux/amd64", directory, {}, directory / "job"
                )
            self.assertEqual(transport.call_args.args[1], "rm")


class RepositoryContainerContractTests(unittest.TestCase):
    def test_image_downloads_set_no_new_privileges_before_entering_landlock(self):
        containerfile = (workflow.CONTEXT / "Containerfile").read_text()
        downloads = [line for line in containerfile.splitlines() if "pacman -S" in line]
        self.assertEqual(len(downloads), 4)
        for line in downloads:
            if "pacman -Scc" not in line:
                self.assertIn("setpriv --no-new-privs pacman", line)

    def test_real_repository_has_a_valid_lock_and_dependency_graph(self):
        config.image_lock(workflow.CONTEXT / "image.lock.json")
        for package in recipes.recipes(workflow.ROOT):
            with self.subTest(package=package):
                recipes.build_order(workflow.ROOT, package)


class FakeEngine:
    name = "fixture-engine"
    rootless = True

    def __init__(self, failures=(), metadata_inputs=None):
        self.failures = set(failures)
        self.jobs = []
        self.removed = []
        self.metadata_inputs = metadata_inputs or {"install": [], "changelog": []}

    def image(self, context, lock, target, output):
        return {"reference": target, "id": f"sha256:{target}"}

    def job(self, image, phase, package, platform, snapshot, archives, output, commit=None):
        self.jobs.append((phase, package, image))
        output.mkdir(parents=True)
        (output / "console.log").write_text("fixture output\n")
        if phase == "build":
            archive = output / f"{package}-1-1-x86_64.pkg.tar.zst"
            archive.write_bytes(b"fixture archive")
            write_manifest(output, [archive])
        if phase == "srcinfo":
            target = output / package / ".SRCINFO"
            target.parent.mkdir()
            target.write_text(f"pkgname = {package}\n")
        if phase == "metadata-inputs":
            (output / "metadata-inputs.json").write_text(json.dumps(self.metadata_inputs))
        return int((phase, package) in self.failures)

    def runtime_tag(self):
        return "installed-fixture"

    def remove_image(self, image):
        self.removed.append(image)


class ContainerWorkflowTests(ContainerFixture):
    def test_discovery_snapshots_only_target_without_building_local_providers(self):
        self.recipe("dependency-local")
        self.recipe("example-local", ["dependency-local"])
        runner = FakeEngine()
        output = self.root / "results"
        workflow.validate("discover", "example-local", runner, output, root=self.root)
        self.assertEqual(runner.jobs, [("discover", "example-local", "sha256:tools")])
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["dependency_order"], [])
        manifest = json.loads((output / "sources.json").read_text())
        self.assertFalse(any("dependency-local" in path for path in manifest))
        self.assertIn("packages/example-local/input.patch", manifest)

    def test_discovery_failure_preserves_failed_stage_and_diagnostics(self):
        output = self.root / "results"
        runner = FakeEngine({("discover", "example-local")})
        with self.assertRaisesRegex(RuntimeError, "Container discover failed"):
            workflow.validate("discover", "example-local", runner, output, root=self.root)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stages"][0]["returncode"], 1)
        self.assertTrue((output / "discover-example-local/console.log").is_file())

    def test_discovery_cli_requires_a_selected_recipe_before_creating_engine(self):
        with (
            patch("sys.argv", ["containers", "discover"]),
            patch("sys.stderr"),
            patch.object(workflow, "Engine") as engine,
            self.assertRaises(SystemExit),
        ):
            workflow.main()
        engine.assert_not_called()

    def test_metadata_probe_copies_only_validated_side_inputs(self):
        directory = self.root / "packages/example-local"
        (directory / "custom.install").write_text("post_install() { :; }\n")
        (directory / "private.txt").write_text("not an input\n")
        output = self.root / "results"
        runner = FakeEngine(metadata_inputs={"install": ["custom.install"], "changelog": []})
        workflow.validate("srcinfo", "example-local", runner, output, root=self.root)
        manifest = json.loads((output / "sources.json").read_text())
        self.assertIn("packages/example-local/custom.install", manifest)
        self.assertNotIn("packages/example-local/private.txt", manifest)
        self.assertEqual([phase for phase, _, _ in runner.jobs], ["metadata-inputs", "srcinfo"])

    def test_metadata_probe_rejects_unsafe_or_malformed_requests(self):
        for index, fields in enumerate(
            (
                {"install": ["../../secret"], "changelog": []},
                {"install": "wrong type", "changelog": []},
                {"install": [], "changelog": [], "unknown": []},
            )
        ):
            runner = FakeEngine(metadata_inputs=fields)
            output = self.root / f"results-{index}"
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                workflow.validate("srcinfo", "example-local", runner, output, root=self.root)
            self.assertEqual(len(runner.jobs), 1)
            self.assertEqual(json.loads((output / "report.json").read_text())["status"], "failed")

    def test_metadata_is_exported_only_to_the_selected_recipe(self):
        self.recipe("other-local")
        original = (self.root / "packages/other-local/.SRCINFO").read_text()
        output = self.root / "results"
        workflow.validate("srcinfo", "example-local", FakeEngine(), output, root=self.root)
        self.assertEqual(
            (self.root / "packages/example-local/.SRCINFO").read_text(), "pkgname = example-local\n"
        )
        self.assertEqual((self.root / "packages/other-local/.SRCINFO").read_text(), original)

    def test_metadata_export_refuses_concurrent_pkgbuild_edits(self):
        output = self.root / "generated"
        (output / "example-local").mkdir(parents=True)
        (output / "example-local/.SRCINFO").write_text("new metadata\n")
        directory = self.root / "packages/example-local"
        original = (directory / ".SRCINFO").read_text()
        with self.assertRaisesRegex(ValueError, "changed during"):
            workflow.update_srcinfo(
                self.root, output, "example-local", {"packages/example-local/PKGBUILD": "0" * 64}
            )
        self.assertEqual((directory / ".SRCINFO").read_text(), original)

    def test_metadata_export_refuses_concurrent_srcinfo_edits(self):
        directory = self.root / "packages/example-local"
        output = self.root / "generated"
        (output / "example-local").mkdir(parents=True)
        (output / "example-local/.SRCINFO").write_text("pkgname = example-local\n")
        manifest = config.snapshot(self.root, self.root / "snapshot")
        (directory / ".SRCINFO").write_text("concurrent user edit\n")
        with self.assertRaisesRegex(ValueError, ".SRCINFO changed during"):
            workflow.update_srcinfo(self.root, output, "example-local", manifest)
        self.assertEqual((directory / ".SRCINFO").read_text(), "concurrent user edit\n")

    def test_full_workflow_builds_dependency_order_and_tests_installed_runtime_images(self):
        self.recipe("dependency-local")
        self.recipe("example-local", ["dependency-local"])
        runner = FakeEngine()
        output = self.root / "results"
        workflow.validate("test", "example-local", runner, output, root=self.root)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["dependency_order"], ["dependency-local", "example-local"])
        self.assertEqual(
            [phase for phase, _, _ in runner.jobs],
            [
                "build",
                "build",
                "lint-install",
                "lint",
                "lint-install",
                "lint",
                "install",
                "smoke",
                "install",
                "smoke",
            ],
        )
        self.assertTrue(
            all(image == "installed-fixture" for phase, _, image in runner.jobs if phase == "smoke")
        )
        self.assertTrue(
            all(image == "installed-fixture" for phase, _, image in runner.jobs if phase == "lint")
        )
        self.assertEqual(len(runner.removed), 4)
        self.assertEqual(set(report["artifacts"]), {"example-local", "dependency-local"})

    def test_lint_failure_is_not_suppressed_and_does_not_hide_smoke_evidence(self):
        runner = FakeEngine({("lint", "example-local")})
        output = self.root / "results"
        with self.assertRaisesRegex(RuntimeError, "Failed validation gates"):
            workflow.validate("test", "example-local", runner, output, root=self.root)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertIn(("smoke", "example-local", "installed-fixture"), runner.jobs)
        self.assertTrue(report["artifacts"]["example-local"])

    def test_failed_lint_install_never_runs_lint_but_keeps_runtime_evidence(self):
        runner = FakeEngine({("lint-install", "example-local")})
        output = self.root / "results"
        with self.assertRaisesRegex(RuntimeError, "Failed validation gates"):
            workflow.validate("test", "example-local", runner, output, root=self.root)
        self.assertFalse(any(phase == "lint" for phase, _, _ in runner.jobs))
        self.assertIn(("smoke", "example-local", "installed-fixture"), runner.jobs)

    def test_failed_dependency_build_stops_consumers_and_records_failure(self):
        self.recipe("dependency-local")
        self.recipe("example-local", ["dependency-local"])
        runner = FakeEngine({("build", "dependency-local")})
        output = self.root / "results"
        with self.assertRaisesRegex(RuntimeError, "build failed"):
            workflow.validate("build", "example-local", runner, output, root=self.root)
        self.assertEqual(len(runner.jobs), 1)
        self.assertEqual(json.loads((output / "report.json").read_text())["status"], "failed")

    def test_missing_installed_test_fails_before_building(self):
        (self.root / "packages/example-local/tests/installed.py").unlink()
        runner = FakeEngine()
        with self.assertRaisesRegex(ValueError, "installed.py"):
            workflow.validate(
                "test", "example-local", runner, self.root / "results", root=self.root
            )
        self.assertEqual(runner.jobs, [])

    def test_failed_runtime_install_never_runs_an_application(self):
        runner = FakeEngine({("install", "example-local")})
        output = self.root / "results"
        with self.assertRaisesRegex(RuntimeError, "install:example-local"):
            workflow.validate("test", "example-local", runner, output, root=self.root)
        self.assertNotIn("smoke", [phase for phase, _, _ in runner.jobs])
        # Only the successful lint-install image was created and removed.
        self.assertEqual(runner.removed, ["installed-fixture"])

    def test_existing_evidence_is_never_overwritten(self):
        output = self.root / "results"
        output.mkdir()
        sentinel = output / "keep"
        sentinel.write_text("previous evidence")
        with self.assertRaises(FileExistsError):
            workflow.validate("check", None, FakeEngine(), output, root=self.root)
        self.assertEqual(sentinel.read_text(), "previous evidence")


class ContainerWorkerTests(unittest.TestCase):
    def test_archive_lint_installs_runtime_without_installed_test_extras(self):
        with (
            patch.object(worker, "runtime_closure", return_value=["example-local"]),
            patch.object(worker, "install_archives") as archives,
            patch.object(worker, "recipe_config") as config,
            patch.object(worker, "install_missing") as extras,
        ):
            worker.install(Path("/input"), "example-local", test_tools=False)
        archives.assert_called_once_with(["example-local"])
        config.assert_not_called()
        extras.assert_not_called()

    def test_discovery_runs_shared_scanner_as_builder_and_exports_reports(self):
        with (
            patch("sys.argv", ["worker", "discover", "example-local"]),
            patch.object(worker.os, "getuid", return_value=0),
            patch.object(worker, "check_pacman_policy"),
            patch.object(worker, "prepare_workspace", return_value=Path("/work/repository")),
            patch.object(worker, "run") as run,
            patch.object(worker, "install_missing") as install,
        ):
            worker.main()
        run.assert_called_once_with(
            "/usr/bin/python",
            "-B",
            "-m",
            "support.repo",
            "discover",
            "example-local",
            "--output",
            "/output",
            cwd=Path("/work/repository"),
            builder=True,
        )
        install.assert_not_called()

    def test_image_policy_rejects_content_filters_and_disabled_download_sandboxes(self):
        for directive in (
            "NoExtract = usr/share/doc/*",
            "DisableSandbox",
            "DisableSandboxFilesystem",
            "DisableSandboxSyscalls",
        ):
            with (
                self.subTest(directive=directive),
                patch.object(worker, "run", return_value=SimpleNamespace(stdout=directive + "\n")),
                self.assertRaisesRegex(ValueError, "Image disables"),
            ):
                worker.check_pacman_policy()
        with patch.object(
            worker,
            "run",
            return_value=SimpleNamespace(stdout="DownloadUser = alpm\nSigLevel = Required\n"),
        ):
            worker.check_pacman_policy()

    def test_application_commands_drop_identity_and_do_not_inherit_host_environment(self):
        with (
            patch.object(
                worker.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000)
            ),
            patch.object(worker.subprocess, "run") as run,
        ):
            worker.run("application", builder=True)
        options = run.call_args.kwargs
        self.assertEqual(
            (options["user"], options["group"], options["extra_groups"]), (1000, 1000, [])
        )
        self.assertEqual(options["env"]["HOME"], "/home/builder")
        self.assertEqual(options["env"]["TMPDIR"], "/work/tmp")
        self.assertNotIn("SSH_AUTH_SOCK", options["env"])

    def test_satisfied_dependencies_are_not_replaced_by_repository_packages(self):
        with patch.object(
            worker, "run", return_value=SimpleNamespace(returncode=0, stdout="")
        ) as run:
            worker.install_missing(["interface>=1", "interface<3"])
        self.assertEqual(run.call_count, 1)

    def test_only_missing_dependencies_are_installed(self):
        result = SimpleNamespace(returncode=127, stdout="library>=1\n")
        with patch.object(worker, "run", return_value=result) as run:
            worker.install_missing(["library>=1", "interface<3"])
        self.assertEqual(
            run.call_args.args,
            (
                "pacman",
                "-S",
                "--disable-download-timeout",
                "--needed",
                "--noconfirm",
                "--",
                "library>=1",
            ),
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 900)

    def test_invalid_dependency_tokens_never_reach_pacman(self):
        with patch.object(worker, "run") as run, self.assertRaises(ValueError):
            worker.install_missing(["--root=/host"])
        run.assert_not_called()

    def test_archive_dependency_downloads_have_the_same_finite_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            archives = Path(temporary)
            directory = archives / "example-local"
            directory.mkdir()
            archive = directory / "example-local-1-1-any.pkg.tar.zst"
            archive.write_bytes(b"fixture")
            write_manifest(directory, [archive])
            with (
                patch.object(worker, "ARCHIVES", archives),
                patch.object(worker, "run") as run,
            ):
                worker.install_archives(["example-local"])
            self.assertEqual(
                run.call_args.args,
                (
                    "pacman",
                    "-U",
                    "--disable-download-timeout",
                    "--needed",
                    "--noconfirm",
                    "--",
                    archive,
                ),
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 900)

    def test_subprocess_deadline_is_enforced_without_turning_timeout_into_success(self):
        with (
            patch.object(worker.pwd, "getpwnam"),
            patch.object(
                worker.subprocess, "run", side_effect=subprocess.TimeoutExpired("pacman", 900)
            ) as run,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            worker.run("pacman", timeout=900)
        self.assertEqual(run.call_args.kwargs["timeout"], 900)
