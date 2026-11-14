# Disposable Arch validation

The public workflow is `just check [package]`, `just verify [package]`,
`just discover <package>`, `just srcinfo <package>`, `just build <package>`,
and `just test <package>`.
All run in Docker/Podman. `just install <package>` is separate: it runs native
`makepkg --syncdeps --install` in the selected recipe directory without containers
or the installed-package test workflow. See the
[root README](../../README.md) for prerequisites, onboarding, host installation,
and genuine clean-chroot release validation.

## Ownership and contracts

- `support/recipes.py`: canonical discovery, selected recipes, `.SRCINFO` parsing,
  declared local inputs, integrity policy, validation settings, and dependency plans.
- `support/artifacts.py`: exact archive membership and SHA-256 manifests shared by
  build validation, host collection, archive lint, and installation.
- `support/repo.py`: in-container lint, source verification, and regression checks.
- `support/discovery.py`: advisory source leads, coverage inventory, and review reports.
- `support/install.py`: local makepkg build/install with dependency resolution and
  normal pacman prompts; no container validation or engine access.
- `support/containers/config.py`: pinned image lock and source-only snapshots.
- `engine.py`: Docker/Podman transport, image caching, logs, scoped cleanup.
- `worker.py`: container setup and unprivileged recipe/application commands.
- `__main__.py`: job orchestration, evidence, and selected metadata export.

Recipes remain ordinary makepkg inputs and never import this framework.
Application dependencies, commands, tests, exceptions, and limitations belong
beside each recipe, not in a global package registry.

## Package configuration

An optional recipe-local `validation.toml` selects local providers and extra
installed-test tools:

```toml
local_dependencies = ["dependency-local"]
local_build_dependencies = ["build-tool-local"]
test_dependencies = ["zsh"]
```

Unknown fields, invalid names, duplicate entries, unknown recipes, and dependency
cycles fail. These declarations supplement, never replace, real PKGBUILD dependency
declarations; pacman checks versioned dependencies and provisions normally.

The three dependency plans have different meanings:

- Build order follows runtime and build-only edges recursively.
- A recipe's build installation contains its direct local runtime/build providers
  and those providers' runtime closures, not their build-only tools.
- Runtime installation contains the target and only its runtime closure.

Thus a generator needed to build a library is built first, but does not leak into
an application's build environment merely because the application uses that library.
Each recipe receives a fresh build container. Runtime images never inherit the
builder filesystem or build-only package installation.

`tests/installed.py` runs with `/usr/bin/python -B` as UID/GID 1000 from an empty
writable directory. Test actual installed paths, isolate application state, and
own/stop every child process. Fail on absent tools or behavior; do not start
persistent services. Python is already in the runtime harness, which can mask a
missing Python runtime dependency and must be considered during dependency review.
`test_dependencies` adds only genuine test tools, not undeclared runtime requirements.

## Inputs and archives

A snapshot contains shared framework/tests, explicit root configuration, selected
recipe metadata/docs/config/tests, local `source`/`source_<arch>` files, and
`install`/`changelog` inputs. Normal selected jobs include the local build closure;
unrelated unfinished recipes do not block them. Whole-repository check/verify jobs
include all recipes. Metadata generation copies the selected PKGBUILD and recipe
support files without resolving old source declarations, allowing initial or stale
`.SRCINFO` to be regenerated. Top-level PKGBUILD metadata must be self-contained.
An initial unprivileged Bash probe reads only the `install`/`changelog` declarations;
the host validates and copies those exact paths before makepkg generates metadata.
This supports new recipes without copying arbitrary files or parsing Bash with
regular expressions. Both probe and generation are network-disabled and logged.

Discovery snapshots only its selected recipe, never its local-provider closure.
It runs the shared recipe checks and scanner through `support.repo` as the builder.
Makepkg verifies/extracts sources with `--nobuild --noprepare --nodeps`, leaving
downstream `prepare()` transformations unapplied. No build dependencies or language
dependencies are installed. Source-fetcher requirements unavailable in the tools
image fail normally; they are not successful skips. PKGBUILD top-level code and
source verification/version functions remain trusted recipe execution, not inert
data. See [patch-boundary discovery](../../README.md#finding-patch-boundaries)
for report semantics and coverage limitations.

Remote downloads, `src/`, `pkg/`, VCS state, host dotfiles, and caches are not
copied. Declared paths must be regular files without symlinks or path traversal.
Architecture-specific arrays share the same integrity policy as generic sources:
SHA-256, SHA-384, SHA-512, or BLAKE2b; weak-only or skipped integrity is rejected.
One output package matching the `-local` directory is an explicit repository rule.

After makepkg, `artifacts.json` records the exact `--packagelist` filenames and
SHA-256 hashes. Unexpected/missing archives, symlinks, or changed bytes fail.
Collection, lint, and installation all consume this manifest instead of selecting
new archives with independent globs. A recipe-local `namcap.toml` may record exact
reviewed warnings with reasons and package/version/build-package pins. Errors and
PKGBUILD warnings cannot be excepted; stale or changed exceptions fail.

Archive lint first installs the target's runtime closure into a disposable tools
image, without installed-test extras. This gives namcap the actual dependency
owners and libraries rather than false "uninstalled dependency" findings from
the bare lint image. The lint job itself remains network-disabled. This image
is discarded and never reused for installed-app testing.

## Privilege and network boundaries

- Host prerequisites: Python 3.13+, just, and a working engine. Prefer rootless
  operation. Podman is selected when installed, otherwise Docker; a broken engine
  fails rather than silently changing security models. Set `CONTAINER_ENGINE` or
  pass `--engine`. `--require-rootless` rejects rootful engines; otherwise their
  effective host-root authority is warned about and recorded.
- No host workspace, home, credentials, engine socket, package state, or namespaces
  are mounted. Image build contexts contain only explicitly selected framework
  files, not the repository or host caches. Inputs and archives are copied.
- Only image/package setup runs as container root. PKGBUILD evaluation, makepkg,
  linters, tests, and applications drop UID/GID/supplementary groups and receive
  a minimal environment. No PKGBUILD is sourced as root.
- Jobs retain default seccomp/namespace isolation, request `no-new-privileges`,
  a PID limit, and private `/tmp`. There are no privileged flags, host namespaces,
  extra capabilities, published ports, or sandbox-disable flags. Container-root
  default capabilities remain available for package setup.
- Builder/test `TMPDIR=/work/tmp` is executable and builder-owned without weakening
  `/tmp` protections. Application tests should preserve it or use isolated homes.
- Image/package setup, source verification/extraction (including discovery), and
  builds need network access.
  Repository checks, metadata generation, archive lint, and installed tests use
  `--network none`; loopback still works. Initial image creation may need network
  access for any command. Disabled networking is not proof that reporting code
  was removed, and ordinary provider/network integrations need separate tests.
- Dependency transactions retain signatures and download sandboxing. They tolerate
  dated mirrors' low-speed pauses but have a finite 15-minute overall deadline.
- The official base's active `NoExtract` and `DisableSandbox*` overrides are removed
  before package setup. Validation must install docs/man pages normally and use
  pacman's download sandbox, not inherit image-minimization or compatibility
  exceptions. Every worker resolves pacman configuration and rejects those
  directives; unsupported host kernels fail instead of disabling sandboxing.
   Image-build downloads explicitly set `no_new_privs` with `setpriv` before
   pacman applies Landlock; jobs request the same restriction through the engine.
   This permits Landlock on engines that do not grant the capabilities otherwise
   needed to enter it. If applying Landlock still fails, use a compatible
   engine/host or VM rather than unconfined security profiles, privileged builds,
   or download-sandbox bypasses.

## Evidence and cleanup

Each invocation creates a new `.artifacts/containers/<timestamp>-<id>/`:

- `sources.json`: SHA-256 of copied inputs.
- `report.json`: action/selection, dependency order, engine/rootless status, lock,
  image identities, stage results, archive hashes, overall status, and errors.
- `image-*.log`, `<phase>-<recipe>/console.log`: image-build/job diagnostics,
  including captured subprocess failures.
- `build-<recipe>/`: archives, exact `artifacts.json`, and makepkg logs.
- `srcinfo-<recipe>/<recipe>/.SRCINFO`: generated metadata before host export.
- `metadata-inputs-<recipe>/`: declared side-input probe and its diagnostics.
- `discover-<recipe>/discovery.txt` and `discovery.json`: advisory leads with review
  questions, source-file hashes, exclusions, and errors. Leads alone never fail;
  incomplete scans do, after saving partial reports. Extraction failures retain
  console diagnostics and a failed stage rather than a misleading empty scan.

Use `python -B -m support.containers <action> [package] --output <new-directory>`
to select a different evidence path. Existing output directories are never
overwritten. Metadata export changes only the selected recipe and refuses a
concurrently edited PKGBUILD. Nothing is installed on the host.

Build failures stop dependent builds. Archive-lint findings do not suppress runtime
tests, but still fail the invocation. Failed jobs keep their reports/artifacts.
Only invocation-owned containers and temporary installed images are removed;
shared images remain cached, with no global prune. Artifact-export failure keeps
the container and prints its ID for recovery. Installation images are committed
only after successful artifact export, avoiding orphan images on export failure.

## Image maintenance and validation scope

`image.lock.json` pins the official Arch x86_64 base by digest and a dated Arch
repository snapshot. Updating either is a reviewed maintenance operation: verify
provenance/digest, choose a usable snapshot, rebuild both images, and rerun checks.
Never substitute mutable tags, disable signatures, or weaken download sandboxing.
The Containerfile's inert `scratch` default cannot build without validated inputs
supplied by the helper. Cache identities hash the lock and every in-image input.

Pinning constrains inputs; it is not verified reproducibility or offline building.
Dependency fetching still follows each recipe's frozen/lifecycle policy. Exercise
each actual engine/backend before claiming support is verified; mocked command
tests are not real rootless Docker or Podman evidence. Keep detailed run evidence
in artifacts rather than dated documentation snapshots.

Containers do not replace genuine devtools clean-chroot builds, live service
lifecycle, interactive/browser/provider checks, or live privacy audits. Use an
appropriately configured Arch host, VM, or CI runner for those gates, not
privileged containers or disabled sandboxes.

Run `python -B -m tests.container_smoke` for an opt-in real-engine framework test.
It uses a tiny synthetic recipe with architecture-specific SHA-512 sources,
non-source install/changelog files, metadata bootstrapping, archive lint, fresh
installation, discovery of verified sources before `prepare()` transformations,
and a deliberately failed metadata probe whose diagnostics and
unchanged host metadata are asserted. Inputs and evidence remain under
`.artifacts/framework-smoke/`, including on failure. It does not build applications.
