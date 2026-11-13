# Disposable Arch validation

This is generic repository infrastructure. Application commands, dependencies,
assertions, exceptions, and validation limitations belong beside each recipe.
Recipes remain standalone makepkg inputs; none sources this framework.

## Commands

```text
just container-check
just container-build <pkgname>
just container-test <pkgname>
```

The host needs Python 3.13+, just, and a working Docker or Podman installation.
It need not be Arch or have makepkg, Bun, Node.js, or the lint toolchain installed.
All supported images currently target Linux x86_64. Engine selection prefers
Podman when installed, otherwise Docker; an unusable engine fails rather than
silently switching to a different security model. Override explicitly:

```sh
CONTAINER_ENGINE=podman just container-check
python -B -m support.containers check --engine docker --require-rootless
```

`--require-rootless` rejects a rootful daemon. Without it, rootful operation emits
a warning and is recorded in the report. Normal Docker-group membership is
effectively host-root access. The framework does not install/configure an
engine, alter its permissions, enable its service, or invoke host sudo.

`container-check` prepares the tools image and runs `just check`. `container-build`
builds each local dependency in topological order, then the selected recipe.
Each build runs source verification and makepkg's normal build/check/package
phases. All archives are passed through the existing strict `package-lint` path.
An optional recipe-local `namcap.toml` records exact reviewed warnings, their
reasons, the package name/version, and audited build-package versions. The shared
archive checker verifies those pins against `.PKGINFO`/`.BUILDINFO` and rejects
new, changed, duplicated, or missing findings. Errors cannot be excepted, and
PKGBUILD lint does not use archive exceptions. This file is copied and hashed in
the source snapshot; no package-specific exception lives in the framework.

`container-test` always performs a fresh build; it does not pick up an arbitrary
old archive. It additionally creates a fresh runtime container for each built
recipe, installs its local runtime closure and declared runtime dependencies
with pacman, snapshots that disposable installation, and runs its installed test
in a new network-disabled container. An archive-lint failure does not prevent
collecting runtime evidence, but the overall command still fails. A failed build
stops dependent builds instead of testing incomplete or stale output.

## Adding a package

1. Add a self-contained `packages/<name>-local/PKGBUILD`, local sources, and
   generated `.SRCINFO`. Keep the README and Python regression tests there.
2. Add `packages/<name>-local/tests/installed.py`. It is run by `/usr/bin/python
   -B` as UID/GID 1000, from an empty writable directory. Exercise the actual
   installed paths, isolate application state in a temporary home, own and stop
   every spawned process, and fail on missing tools or behavior. Never start a
   persistent service or use host user state. Its exception/nonzero exit fails
   the job. Python is a test-harness dependency present in the runtime base;
   account for that when reviewing missing runtime dependencies.
3. If needed, add `validation.toml` beside the recipe:

   ```toml
   local_dependencies = ["dependency-local"]
   local_build_dependencies = ["build-tool-local"]
   test_dependencies = ["zsh"]
   ```

   All settings are optional lists; unknown fields, invalid names, duplicates,
   unknown recipes, and dependency cycles fail. `local_dependencies` selects
   in-repository providers needed at runtime and build time; build-only local
   dependencies belong in `local_build_dependencies`. These supplement, not
   replace, real dependency declarations in the PKGBUILD. Pacman's versioned
   dependency checks still apply. `test_dependencies` adds repository tools to
   the disposable test installation only; do not use it to conceal missing
   application runtime dependencies.
4. Run the container commands. No package names, application commands, or
   dependency ordering should be added to shared scripts or root tests.

Root framework tests use synthetic recipes. Existing recipe Python tests are
discovered normally by `just test`; `installed.py` is deliberately a separate
contract so native repository checks never accidentally run installed software.

## Input, privilege, and network boundaries

- Inputs are copied, not host-mounted. A source-only snapshot includes shared
  framework/tests, root repository configuration, recipe metadata/docs/config,
  recipe tests, and local files declared by committed `.SRCINFO`. Remote source
  downloads, `src/`, `pkg/`, VCS state, arbitrary root dotfiles, caches, and host
  credentials are not copied. Symlinked inputs and local-source path traversal
  fail explicitly. Extra fixtures should live under recipe `tests/`, not a new
  global allowlist or package-specific framework branch.
- The image build installs the shared toolchain as container root. Job setup
  installs packages as container root. PKGBUILD evaluation (including dependency
  metadata extraction), makepkg, linters, tests, and application processes run as
  the unprivileged builder with a minimal environment. No host tokens, sockets,
  user configuration, or package-manager state are forwarded.
- Builder/test `TMPDIR` points to the disposable, builder-owned `/work/tmp` so
  compiled test programs can execute without removing `/tmp` mount protections.
  Installed test helpers should preserve that environment or use their own
  isolated writable home for temporary executable files.
- The tools image contains generic development tools; the runtime image does
  not inherit the builder's filesystem or installed build dependencies. Local
  package archives are explicitly transferred and installed with pacman.
- Default seccomp and namespace isolation remain in effect. Jobs request
  `no-new-privileges`, a PID limit, and a private temporary filesystem. There are
  no privileged containers, host namespaces, added capabilities, sandbox-disable
  flags, or published ports. Docker/Podman's default container-root capabilities
  are retained for pacman setup; application subprocesses drop UID, GID, and
  supplementary groups. A container is not a VM or a proof against malicious
  kernel exploits or every form of network reporting.
- Dependency setup tolerates dated mirrors that pause beyond pacman's default
  low-speed cutoff, but each install has a 15-minute overall deadline. This only
  changes transfer timeout handling: package signatures, checksum verification,
  the download sandbox, and container isolation remain enabled. A stalled or
  failed transaction still fails the job and preserves its diagnostics.
- Image/package setup and builds need network access. Repository checks, archive
  lint, and installed tests run with `--network none`. Loopback server tests work;
  external provider/integration tests require separate, explicitly documented
  validation. Runtime privacy tests need additional evidence beyond disabled
  networking to establish that reporting implementations are actually removed.

## Evidence and cleanup

Each invocation creates `.artifacts/containers/<timestamp>-<id>/`. Use
`python -B -m support.containers test <pkgname> --output <new-directory>` to choose
another new location. Existing output directories are never overwritten.

- `sources.json`: SHA-256 of every copied input.
- `report.json`: action, dependency order, engine/rootless status, locked base and
  repository date, local image identities, stage exit codes, archive hashes,
  final status, and any orchestration error.
- `image-*.log`, `<phase>-<recipe>/console.log`: image-build and job output.
- `build-<recipe>/`: resulting archives and makepkg phase logs, including on
  failed jobs when available.

Only containers and temporary runtime-image snapshots created by this invocation
are removed. Shared tools/runtime images remain cached. No global engine prune
or unrelated container/image deletion is performed. Logs and artifacts remain
even when validation fails or a later dependency cannot build.
If exporting artifacts fails, the job container is preserved and its identifier
is printed for manual recovery instead of deleting the only remaining copy.

The image lock and dated Arch repositories constrain inputs; this is not a claim
of verified reproducible or offline builds. Dependency fetching by each recipe
must still use its pinned, integrity-checked lockfiles and lifecycle policy.

## Image maintenance and release scope

`image.lock.json` pins the official Arch Linux x86_64 base by registry manifest
digest and the Arch Linux Archive repository date. The initial manifest was
resolved from the official `library/archlinux` registry on 2026-11-13; its upstream
image revision is `457808cdaf8ca50c14692f1099c2efd63049bd00` (2026-11-09).
Changing either input is an explicit reviewed maintenance operation: verify the
official image provenance/digest, choose a usable dated repository snapshot,
update the lock, rebuild both images, and rerun validation. Never substitute a
mutable image tag, disable signature checks, or turn off pacman's download
sandbox to get a build to pass.

Local image cache names are derived from the lock, Containerfile, and in-image
worker code. Rootless Podman and Docker share the same CLI contract, but each
engine/backend must be exercised before claiming it is verified.
The Containerfile's inert `scratch` default deliberately cannot build on its own:
use the helper, which validates the lock and supplies the pinned base explicitly.

A passing container run does not replace the repository's genuine devtools
clean-chroot gate. Installed systemd lifecycle, interactive TUI/browser behavior,
model-provider requests, and live network audits remain separate recipe-specific
release gates. Run privileged clean-chroot jobs on an appropriately configured
Arch host, disposable VM, or CI runner rather than weakening container isolation.

## Framework validation evidence (2026-11-13)

The native checks and Docker `container-check` pass. The latter exercises all
53 generic/recipe tests plus format, lint, syntax, metadata, and local-source
integrity gates inside the pinned Arch tools image, without network access.
Evidence is retained in `.artifacts/containers/20261113T004916Z-6862ff5a/`.
Both tools and runtime images have been built with Docker's default sandbox.
Real failed build/install/test jobs retain their logs and fail the invocation;
mocked tests also cover dependency ordering, runtime closures, cleanup/export
failures, rootless enforcement, and both engines' command formats.

The available Docker daemon is rootful. Rootless Docker and real Podman execution
have not been validated here; unit tests are not a substitute for those runs.
Package build, archive, and application-runtime outcomes are recorded beside each
recipe rather than treated as generic framework success.
