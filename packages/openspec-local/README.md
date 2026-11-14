# openspec-local

Packages [OpenSpec](https://github.com/Fission-AI/OpenSpec), a Node.js CLI for
spec-driven development. Pacman owns the installed CLI and its dependencies.
Run `openspec init` explicitly inside projects to install agent skills;
package installation does not initialize projects or change user configuration.

## Build and contents

Build with `just build openspec-local`. The PKGBUILD requires Arch's Node.js,
pnpm, and Python; its checks also require Bash, Zsh, Fish, Git, and npm. Runtime uses
Arch's Node.js rather than a bundled executable. Git is optional for Git-backed
specification stores.

The release archive and its integrity-bearing pnpm lockfile pin the sources
and dependency graph. Dependency fetching requires network access. Lifecycle
scripts are disabled; `build.js` explicitly invokes the locked TypeScript
compiler. The package-manager field is removed before resolution so pnpm uses
the Arch-managed tool instead of downloading another pnpm. `deploy.py` copies the
installed production dependency closure without another resolution step, using
relative links contained inside the package. Its root files follow upstream's
published file selection.
Development tools are not installed. Offline and reproducible builds are not
claimed.
Inquirer's optional `@types/node` peer is type-only and is omitted together with
its `undici-types` dependency. The Node.js minimum also covers the locked
Inquirer runtime's requirement, not just OpenSpec's own engine declaration.

The package installs `/usr/bin/openspec`, its private runtime under
`/usr/lib/openspec`, the MIT license, upstream README, schemas, and upstream-generated
Bash, Zsh, and Fish completions in Arch's standard system locations. Completion
generation runs during `build()` with a disposable home. No service, desktop
entry, icon, or MIME registration is appropriate for this CLI. Upstream does not
provide a man-page generator; no downstream manual is synthesized.

System completion files require no `openspec completion install` invocation.
That upstream command is a separate, explicit user-level installer and can edit
shell startup files; it is not run by this recipe or any package hook.

## Privacy and software updates

`prepare.py` checks complete hashes of the audited upstream boundaries before
replacing any file. Drift fails before edits, including the package-manager
manifest change. The replacement modules enforce policy independently of
environment variables and configuration:

- `src/telemetry/index.ts`: reporting, notices, and tracking-ID creation are
  inert. Enabling telemetry in configuration cannot restore the removed sender.
- `src/core/version-check.ts`: automatic and explicit registry checks return
  disabled; software upgrades and upgrade subprocesses cannot run. Version
  reports identify pacman as the owner.
- `src/commands/install-standalone.ts`: `openspec install [version]` refuses and
  directs the user to `pacman -Syu`, without downloading or executing installers.
- `src/core/standalone-tip.ts`: the parallel-installer advertisement is inert.
- `src/core/github-copilot/cloud-agent.ts`: generated cloud workflows verify a
  pre-provisioned CLI instead of installing an unpinned npm copy; agent instructions
require administrator provisioning. Legacy recognition bodies remain intact.

The generated Copilot cloud setup requires an environment already containing
OpenSpec. The default GitHub-hosted Ubuntu runner does not provide this Arch
package; administrators must provision a suitable environment explicitly.

`openspec update` remains available: it regenerates project instruction files
from the installed CLI, not installed software. Its CLI upgrade check is disabled.
Existing user configuration is neither rewritten nor removed by package
installation. Legacy telemetry configuration readers remain for compatibility;
the reporting module cannot mint or transmit identifiers.

The source audit covers CLI hooks, reporting, registry checks, standalone
installers, upgrade children, and production dependencies. User-requested Git
stores and feedback/browser integration remain functional. External agents,
Git remotes, browsers, and separately installed programs remain outside this
package's control. Source searches and network-disabled container tests do not
establish complete network isolation.

## Validation and maintenance

Run `just check openspec-local`, `just verify openspec-local`, and
`just test openspec-local`. These commands run in containers; the test workflow
builds, lints, installs, and exercises the archive. Container validation
supplements the genuine clean-chroot release gate: run `extra-x86_64-build`
from this recipe's directory on an appropriately configured Arch host or VM.

`check()` runs upstream lint, compilation through the build, and the upstream
test suite except the four reporting/updater/installer suites and the version-report
and telemetry-specific global-config cases whose contracts are deliberately
replaced. The name filter in `check()` identifies those cases explicitly.
Upstream's npm lifecycle tests run offline against disposable local fixtures and
the reviewed `build.js` compiler hook; only those tests permit npm Git-file
installation. Real dependency installation still disables lifecycle scripts.
`policy.test.mjs` exercises the staged replacement
boundaries with opt-ins enabled and traps network/process APIs, including
swallowed attempts. It checks installer refusal, absence of tracking writes,
disabled explicit checks, production-only dependencies, and contained links.
Recipe-local Python tests check drift rejection and recipe structure.
`tests/installed.py` exercises version reporting, initialization, change status,
project-file updates, cloud provisioning templates, dynamic completion,
shell syntax/loading, and package files
as an ordinary user in a disposable home. `validation.toml` keeps optional shells
and bash-completion confined to installed validation.

Installed checks require the README's package ownership and read its contents.
The repository's validation images clear the official Arch base image's
documentation exclusions. In other runtimes that deliberately omit the README,
the check requires pacman's exact `NoExtract` entry `usr/share/doc/*`; archive
validation verifies the shipped file independently. This narrow documentation
exception does not permit missing CLI, license, or completion files.

Release validation must include the clean-chroot build and any live external
integrations required by the intended deployment. Upstream's opt-in historical
release-upgrade matrix downloads separate npm installations and is outside the
locked package build; its default-disabled cases are not counted as validation.
Detailed outcomes and failed
gates belong in validation artifacts, not this README. On version bumps, re-audit
all network/update boundaries and transitive runtime dependencies, review the
replacement modules against changed CLI imports, refresh exact upstream hashes,
review upstream test exclusions, and regenerate checksums and `.SRCINFO`.
