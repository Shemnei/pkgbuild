# Local Arch packages

One standalone makepkg recipe per `packages/<pkgname>/` directory, including
sources, downstream changes, tests, README, and committed `.SRCINFO`. Each recipe
produces one package with the same name as its directory, ending in `-local`.
Pacman owns updates; recipes disable upstream self-updaters and outbound reporting
where they can enforce that policy. See [AGENTS.md](AGENTS.md) and recipe READMEs.

## Container validation

The host needs Python 3.13+, just, and working Docker or Podman. It does not need
Arch, makepkg, or the application build/lint toolchain. Prefer a rootless engine;
rootful Docker access is effectively host-root access. Never run `just` with sudo.
The engine must support pacman's Landlock download sandbox during both image
builds and jobs. If Docker/BuildKit rejects it with `Operation not permitted`, use
a compatible engine/host or VM; there is no sandbox-disable fallback.

```sh
just check                         # shared and all recipe lint/regression checks
just check <pkgname>                # shared and selected recipe checks
just verify <pkgname>               # verify declared local and remote sources
just discover <pkgname>             # advisory patch-boundary leads from unpatched sources
just build <pkgname>                # fresh dependency builds and strict archive lint
just test <pkgname>                 # build/lint, then fresh installed-package tests
just srcinfo <pkgname>              # generate and update only this recipe's .SRCINFO
```

Omit the package from `verify` to verify all recipes. `test` always builds; it
does not select an arbitrary old archive. `check` includes the generic tests and
recipe-local `test*.py` suites, isolated in separate Python processes. It does
not build upstream. PKGBUILD `check()` runs the recipe's upstream/build checks.
`tests/installed.py` is a separate installed-software contract, not a regression
test discovered by `check`.

Choose an engine explicitly with `CONTAINER_ENGINE=docker` or
`CONTAINER_ENGINE=podman`. To enforce rootless operation:

```sh
python -B -m support.containers check --engine podman --require-rootless
```

The old native validation wrappers and `container-*` aliases have been removed.
The commands above are the canonical validation workflow; local installation
is a separate operation described below.
The internal `support.repo` module is the checker executed inside the tools
container, not a second supported host workflow.

## Isolation and evidence

Inputs are copied, never host-mounted. No engine sockets, credentials, home
directories, or host package state are forwarded. Images use a digest-pinned
official Arch base and dated Arch repositories. Image creation, source fetching,
package setup, and builds need network access. Checks, metadata generation,
archive lint, and installed tests run without external container networking.
Image preparation may still require network access even for an offline check.
Default container sandboxes remain enabled.

Every container invocation preserves `.artifacts/containers/<run>/` with input hashes,
image identities, logs, archive manifests/hashes, and `report.json`. Missing
tools, stale metadata/checksums, failed tests, and unresolved namcap findings
fail the command. Runtime tests still collect evidence after archive-lint
findings. Failed artifact export preserves the container for manual recovery.

Recipe-local `namcap.toml` may except exact reviewed archive warnings with reasons,
package/version pins, and audited build-package versions. New, changed, duplicate,
or disappeared findings fail. Archive exceptions never apply to PKGBUILD lint.

See [the framework guide](support/containers/README.md) for input contracts,
local dependency providers, test dependencies, security boundaries, and images.

## Installing and release validation

Container commands never install software on the host. For a local build and
installation on an Arch-compatible system, install `base-devel`, configure
makepkg's pacman privilege helper (normally sudo), and run as your normal user
in an interactive terminal:

```sh
just install <pkgname>
```

This runs `makepkg --syncdeps --install` directly in `packages/<pkgname>/`.
Makepkg downloads and verifies the PKGBUILD's sources, installs missing build and
runtime dependencies from configured pacman repositories, builds locally when
needed, and installs the resulting package with normal pacman prompts. Normal
PKGBUILD `check()` steps remain enabled. Existing package reuse follows makepkg's
standard behavior; no prior archive or container run is required.

No container engine or separate installed-package tests are invoked. The host
needs the recipe's build tools and dependencies, unlike container validation.
Dependencies unavailable in configured repositories, including local providers,
must be installed separately. No service is enabled automatically. Use
`just test <pkgname>` separately for isolated installed-package validation.

Alternatively, inspect a successful run's exact archives and install manually:

```sh
sudo pacman -U /absolute/path/to/package-local-version-release-x86_64.pkg.tar.zst
```

Include required local dependency archives in the same transaction. Pacman keeps
its normal confirmation prompts. No service is enabled automatically.

Docker covers routine validation, **not every release gate**. A genuine clean
Arch chroot remains required; on a suitably configured Arch host or disposable VM,
run devtools from the recipe directory:

```sh
extra-x86_64-build
# If a dependency must use a locally built provider:
extra-x86_64-build -- -I /absolute/path/to/dependency-local.pkg.tar.zst
```

Devtools requires sudo, working mirrors/keyrings, network access, disk space, and
dependencies available in its configured repositories or injected with `-I`.
Host-installed dependencies are not inherited. Real systemd lifecycle, interactive
TUI/browser, provider requests, and live privacy audits remain recipe-specific
release gates. Do not use privileged containers or disable sandboxes to imitate
these gates. Containers do not establish network isolation or reproducible builds.

## Adding recipes

```sh
just new example-local
```

This creates a small standalone skeleton based on Arch's `PKGBUILD.proto`, a README
checklist, and an installed test. It never overwrites an existing path. The
checksum, build/check/package phases, and installed test are deliberately
unfinished and fail until implemented; no passing placeholder or `SKIP` is used.

1. Replace every TODO. Pin releases and dependency lockfiles; use verified SHA-256,
   SHA-384, SHA-512, or BLAKE2b checksums, including architecture-specific sources.
2. Keep all local inputs beside the recipe. Declare ordinary build inputs in
   `source`; makepkg's `install`/`changelog` inputs are also copied automatically.
3. Run `just discover example-local` to find source leads for closer review.
   Audit updater/reporting implementations and document enforcement, limitations,
   supported upstream integration, intentional omissions, and meaningful checks.
4. Add regression tests under the recipe's `tests/`. Implement `tests/installed.py`
   against actual installed paths with isolated application state.
5. Declare optional local providers and installed-test tools in `validation.toml`.
   No shared package registry needs editing.
6. Run `just srcinfo example-local`, `just check example-local`,
   `just verify example-local`, and `just test example-local`; then complete the
   genuine clean-chroot and application-specific release gates.

Metadata generation is selected-recipe-only and works before `.SRCINFO` exists
or after source declarations change. It evaluates the self-contained PKGBUILD as
the unprivileged builder, without copying old declared sources; keep metadata
declarations self-contained rather than sourcing external files at top level.
An unprivileged probe identifies install/changelog paths for validated copying.
Generation refuses to overwrite concurrently changed PKGBUILD or metadata inputs.
Other selected commands snapshot the dependency closure, not unrelated recipes.

Recipes never source shared repository helpers. Shared tools/tests/documentation
stay package-agnostic. Keep build evidence in artifacts, not README build diaries.

### Finding patch boundaries

After pinning sources/checksums and generating `.SRCINFO`, run
`just discover <pkgname>` for new recipes and version updates. It checks metadata
and integrity, then uses makepkg to fetch, verify, and extract the selected
recipe's active-architecture sources without running `prepare()`, `build()`,
`check()`, or `package()`. It does not install build dependencies or fetch a
language dependency tree. The PKGBUILD remains trusted executable recipe code:
top-level code and makepkg's source verification/version functions can still run
as the unprivileged builder. This is not a safe way to inspect untrusted recipes.

The container evidence directory's `discover-<pkgname>/discovery.txt` groups
file/line leads and review questions for updates, reporting, runtime installers,
child processes, background/browser workers, persisted reporting settings, and
upstream integration files/generators. `discovery.json` also records scanned-file
SHA-256 hashes, source/test/documentation/dependency-metadata labels, exclusions,
and errors. Evidence is quoted as data, not executable commands. Only the selected
recipe is inspected; local dependency providers are not built or scanned.

These are heuristic review leads, **not required patches or a compliance verdict**.
Ordinary networking and local statistics must remain available. Follow callers,
configuration, and relevant dependency implementations; prefer supported upstream
switches before small implementation-boundary patches. Keep review conclusions,
enforcement, and meaningful regression tests beside the recipe. Newly introduced
boundaries still need discovery even when existing patch-input hashes match.

Reporting leads include analytics routes, telemetry factories/custom callbacks,
log-upload and Insights-sync settings, and diagnostic identifiers/fields carried
alongside ordinary API requests. Update/install leads include manual updater
commands, writable executable caches, GPU binary managers, and remote executable
components. Disabled forms are still leads: trace whether a setting can undo them.
Analytics erasure routes, functional session/device IDs, service quota receipts,
and model-data downloads are not automatically telemetry or software updates.
When an audit finds a missed pattern, extend the shared rules in
`support/discovery.py` and package-agnostic regressions in `tests/test_discovery.py`;
keep package-specific enforcement and evidence beside the recipe.

The scan includes hidden, vendored, and generated text present in the extracted
tree, regardless of whether it is shipped. Binary files identified by NUL bytes,
symlinks (including makepkg's links to local inputs), and `.git` internals are
listed exclusions, not followed. Missing/empty text trees, unreadable entries,
invalid UTF-8, special files, and text exceeding 16 MiB fail and preserve partial
scan evidence. Findings do not fail discovery. No-match results do not establish
privacy compliance; absent dependency code, other architectures, binaries, runtime
behavior, and the final patched application require separate review and testing.

## Commits

Use `<type>(<pkgname>): <summary>` with the full `-local` directory name. Commit
each package separately with its metadata, sources, tests, and documentation.
Shared repository changes use separate, unscoped Conventional Commits.
