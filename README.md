# Local Arch packages

One self-contained recipe per `packages/<pkgname>/` directory. All packages
end in `-local`; pacman owns updates. Upstream self-updaters and outbound
telemetry are disabled wherever the package can enforce that policy.
See [AGENTS.md](AGENTS.md) for contributor requirements and each recipe's
README for contents, prerequisites, usage, policy, trade-offs, and validation gaps.

Man pages and shell completions are shipped only when upstream provides files
or a supported generator. Missing integrations are documented, not recreated
downstream. Recipe READMEs describe the package, not its change/build history;
detailed validation evidence belongs in reports and artifacts.

## Tools and checks

On Arch, install the shared tooling explicitly:

```sh
sudo pacman -S --needed base-devel just python ruff shellcheck shfmt namcap devtools nodejs codespell typos
just check                         # format, syntax, lints, metadata, local tests
just verify                        # download and verify all declared sources
```

Commands for a selected recipe (replace `<pkgname>` with its directory name):

```text
just build <pkgname>
just install <pkgname>
just package-lint <pkgname>
just chroot <pkgname>
just container-check
just container-build <pkgname>
just container-test <pkgname>
```

`just check` is offline and does not build upstream. `just test` runs generic
repository tests from `tests/` and each recipe's Python tests from
`packages/<pkgname>/tests/`, in separate processes. Recipe-local modules can
share filenames without colliding. A successful check does not prove that a
full package builds. Each PKGBUILD's `check()` performs its applicable upstream
checks and runtime tests, documented beside the recipe.

`namcap` errors and unreviewed warnings are failures; missing tools are not
silently skipped. A recipe may record audited archive warnings in `namcap.toml`,
with exact diagnostic text and a reason for every entry. Exceptions are pinned
to the archive's package name/version and audited build-package versions from
`.BUILDINFO`; changed, duplicated, or disappeared findings fail. They never
apply to PKGBUILD lint. Upstream source-lint exceptions likewise require exact,
release-specific matches, not blanket rule suppressions.

## Building and installing

Run as your normal user from the repository root; do not run `just` with sudo.

`just build <pkgname>` runs `makepkg --cleanbuild --log` in the selected recipe.
Install its build and runtime dependencies first: this command deliberately
does not install dependencies or the resulting package.

`just install <pkgname>` adds `--syncdeps --install`. It installs missing
dependencies from configured pacman repositories, builds and checks the package,
then installs it with pacman. Normal confirmation and privilege prompts remain
enabled. Dependencies unavailable in configured repositories must be installed
separately. See each recipe's README for required versions and dependency order.

No service is automatically enabled, no signing identity is invented, and no
remote Git or binary pacman repository is created.

## Container validation

Use Docker or Podman to run the normal checks and package builds without
installing dependencies or packages on the host. No host workspace, home,
credentials, or engine socket is mounted into containers. Prefer a rootless
engine; access to a rootful Docker daemon remains effectively host-root access.
Select an engine with `CONTAINER_ENGINE=podman` or `CONTAINER_ENGINE=docker`.
Do not run these commands with sudo.

- `just container-check`: run `just check` inside the shared Arch tools image.
- `just container-build <pkgname>`: build the recipe and its declared local
  dependencies in order, verify sources, run PKGBUILD checks, and lint archives.
- `just container-test <pkgname>`: also install the archives in fresh runtime
  containers and run recipe-local `tests/installed.py` as an ordinary user.

Images use a digest-pinned official Arch base and a dated Arch repository
snapshot. Image creation, dependency installation, and application builds need
network access. Checks, archive lint, and installed application tests run with
container networking disabled (loopback still works). Default container
sandboxes remain enabled; no privileged containers or extra capabilities are
requested. These tests are not a guarantee of application network isolation.

Every invocation creates a new `.artifacts/containers/<run>/` directory with
source hashes, image identities, logs, archives, and a machine-readable report.
Failures remain failures, including unreviewed namcap warnings; runtime tests
still run after archive-lint findings so their evidence is not lost. Nothing is installed
on the host and no existing build tree is reused or deleted.

See [the container framework guide](support/containers/README.md) for package
onboarding, dependency declarations, rootless enforcement, and image maintenance.
Containers do not replace the clean-chroot, service lifecycle, or live browser
release gates documented beside each recipe.

## Clean-chroot builds

`just chroot <pkgname>` runs devtools' `extra-x86_64-build` in the recipe
directory. Devtools uses sudo to create or refresh the base chroot under
`/var/lib/archbuild/extra-x86_64/`, installs declared dependencies inside it, and
builds as an unprivileged build user. The first invocation creates the chroot;
no manual `mkarchroot` setup is needed.

You need working sudo authorization, mirrors/keyrings, network access, and enough
space for the base chroot, its build copy, sources, dependencies, and artifacts.
Host-installed dependencies are not inherited. All declared dependencies must be
available in the chroot's configured repositories. The wrapper currently accepts
only a recipe name; it does not inject locally built dependency archives or
forward additional devtools options. Recipe-specific requirements and blockers
belong in that recipe's README.

Prefer this clean-chroot build for release validation. Successful repository
checks alone are not release evidence: inspect and lint the resulting archive
and perform the recipe's documented runtime checks.

## Adding recipes

1. Create `packages/<name>-local/PKGBUILD` and keep all its local sources there.
2. Pin and checksum sources; declare dependencies and meaningful `check()` steps.
3. Audit all update/reporting paths and document enforcement and limitations in
   the recipe README. Put its regression tests in its own `tests/` directory.
4. Generate `.SRCINFO`, run the global checks, build in a clean chroot, inspect
   the resulting package, and smoke-test it as an unprivileged user.
5. Add `tests/installed.py` for disposable installed-package tests. Declare any
   in-repository dependencies in the recipe's optional `validation.toml`; no
   shared command or package-name registry needs editing.

After changing a recipe input, update its SHA-256 entries and run `just srcinfo`.
Commit `.SRCINFO` with the recipe. Document dependency-fetching network access
and frozen resolution in the recipe; do not claim offline or reproducible builds
without verification. Only reusable framework code, tests, and documentation
belong in the root, `support/`, or `tests/`.

## Commits

Use Conventional Commits with the full package directory name as the scope:
`<type>(<pkgname>): <summary>`. Commit each package addition or change separately,
including its recipe, `.SRCINFO`, sources, tests, and documentation together.
Shared repository changes belong in separate, unscoped Conventional Commits.
