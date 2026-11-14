# Repository instructions

This is an Arch Linux PKGBUILD repository. Keep one recipe in each
`packages/<pkgname>/` directory, including its sources, downstream changes,
tests, and committed `.SRCINFO`. Every package name must end in `-local`.
Shared validation and build support belongs in `support/`; only generic
repository tests belong in `tests/`. Package-specific tests belong in
`packages/<pkgname>/tests/`, and package-specific documentation and guidance
stay beside the recipe. Keep root documentation, tools, and test fixtures
package-agnostic. Recipes must work with makepkg without sourcing global
repository helpers.

## Commits

- Use Conventional Commits: `<type>(<pkgname>): <summary>` for package changes,
  using the full package directory name, including `-local`, as the scope.
- Commit each package addition or change separately. Include its PKGBUILD,
  `.SRCINFO`, sources, tests, and recipe documentation together; never combine
  multiple packages in one commit.
- Keep shared repository tooling, tests, and documentation in separate,
  unscoped Conventional Commits rather than assigning them a package scope.

## Arch packaging and system integration

- Follow current Arch package guidelines, `PKGBUILD(5)`, `makepkg(8)`, and
  applicable language-specific packaging guidelines. Use standard filesystem
  locations, accurate metadata, correctly classified dependencies, appropriate
  permissions, licenses, and Arch's default build/hardening options unless a
  narrow, documented exception is necessary.
- Aim for the best practical system integration within each recipe's scope,
  not merely a working executable. Assess and include supported Bash, Zsh, and
  Fish completions, man pages, useful documentation, systemd units, and XDG
  integration. Desktop entries, icons, MIME associations, and other desktop
  integration are appropriate only when the packaged application supports them;
  this does not authorize adding a desktop client to a CLI/server-only recipe.
- Ship man pages and shell completions only when upstream provides the files or
  a supported generator. Run upstream generators during `build()`, not in
  `package()` or installation hooks. Do not author downstream replacements or
  synthesize missing integrations with tools such as help2man; document their
  absence instead. Install Bash completions in
  `/usr/share/bash-completion/completions/`, Zsh completions
  in `/usr/share/zsh/site-functions/`, and Fish completions in
  `/usr/share/fish/vendor_completions.d/`. Do not modify users' shell startup
  files or require optional shells merely to ship their completion files.
- Declare genuine optional integrations with explanatory `optdepends`. Use
  standard Arch mechanisms for service units, sysusers, tmpfiles, and package
  hooks where needed; avoid custom installers and redundant hooks. Preserve
  administrator configuration with appropriate `backup` entries. Never enable
  services or change existing user configuration automatically.
- Validate shipped integration files and exercise their supported behavior.
  Record provided integrations, intentional omissions, upstream limitations,
  and remaining validation gaps in the recipe README.
- Keep READMEs focused on durable package information, usage, trade-offs, and
  future-version maintenance notes. Do not add changelogs, dated build diaries,
  or host-specific readiness snapshots; retain detailed run evidence in
  validation reports and artifacts.

## Updates and privacy

- Pacman owns installed software. Disable upstream automatic update checks,
  background downloads, self-updaters, automatic upgrades of dependencies,
  and update prompts. Disable manual self-update entry points as well: they
  must direct the user to pacman without downloading or modifying software.
- Disable outbound telemetry, usage analytics, tracking identifiers, and
  remote crash reporting, including bundled dependencies where possible.
  Prefer supported build options; otherwise make small, auditable downstream
  changes at the implementation boundary. Opt-out defaults alone are not
  sufficient when settings can re-enable reporting.
- Audit browser code, backend code, installers, background tasks, and child
  processes. Do not confuse local performance statistics with outbound
  telemetry. Do not disable ordinary user-requested network functionality.
- Record audit findings, enforcement mechanisms, limitations, and test evidence
  in each recipe's README. Never claim complete network isolation from a
  source search or an environment variable. External services and separately
  installed programs remain outside the package's full control.
- Never edit existing user configuration, enable services, or run applications
  as root during installation. Do not weaken sandboxes to make tests pass.

## Sources and builds

- Pin releases and dependency lockfiles. Verify remote and local sources with
  strong checksums; no `SKIP`, mutable latest URLs, or curl-pipe-shell installers.
- Use frozen dependency resolution. Disable dependency lifecycle scripts and
  invoke only reviewed, necessary build steps explicitly. Document any network
  needed for dependency fetching; do not claim offline/reproducible builds
  unless verified. Prefer Arch-managed runtime dependencies.
- Keep build and test work out of `package()`. Do not install development-only
  dependencies or links back into the build directory.
- Fail on upstream drift instead of silently applying partial changes.
  Regenerate `.SRCINFO` and local-source checksums whenever inputs change.

## Validation

Use the generic Docker/Podman workflows in `support/containers/` for routine
isolated lint, build, archive, and installed-package tests. Prefer rootless
engines; never add privileged containers, host workspace/socket mounts, or
sandbox-disable flags. Keep application-specific dependencies in recipe-local
`validation.toml` and installed checks in `tests/installed.py` beside that recipe.
Container validation supplements, not replaces, genuine clean-chroot and
application-specific release validation. Preserve failed reports and artifacts;
never turn missing tools or failing gates into successful skips.

Run every applicable lint, format, syntax, source-integrity, metadata, upstream
type/test, package-content, and runtime check available. Start with `just check`.
Use `just verify`, `just build <pkgname>`, and a clean Arch chroot before
calling a package release-ready. Missing tools and unexplained findings are
failures, not successful skips. Exceptions must be narrow, justified, and
documented beside the affected recipe. Preserve unrelated user work and report
exactly what was and was not verified.

The public `just check`, `verify`, `srcinfo`, `build`, and `test` commands use
containers; keep those validation commands container-only. Use
`just srcinfo <pkgname>` for selected metadata generation and `just new <pkgname>`
for the deliberately unfinished standalone skeleton. `just install <pkgname>`
is the explicit local-install exception: run `makepkg --syncdeps --install` as
the normal user in the selected recipe directory, retaining normal PKGBUILD
`check()` and pacman prompts. It must not invoke containers or the separate
installed-package test workflow, require prior build artifacts, or bypass
confirmation. Dependencies unavailable from pacman repositories must be installed
separately; do not silently build unrelated recipes. Genuine clean-chroot
validation uses devtools on an Arch host or VM, not privileged containers. Shared
recipe contracts belong in `support/`, not in container transport modules.
