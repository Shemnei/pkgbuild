# opencode-local

Source build of OpenCode **2.2.7** for **x86_64**, providing the `opencode`
CLI, TUI, API server, and ACP. No Electron desktop client or embedded browser UI
is built; OpenChamber is packaged separately. Pacman manages application updates
and removal. Existing user configuration is not rewritten.

## Requirements and use

Runtime dependencies are glibc, gcc-libs, ICU, Git, and ripgrep. ICU is required
by the embedded Arch Bun runtime; bundled native modules require gcc-libs.
Git and ripgrep are subprocess dependencies, even when ELF analysis reports
them as unused. External formatters and other user-configured tools must be
installed separately; the package does not automatically fetch formatters.

Building requires Arch's base-devel toolchain, Bun >=1.4.4, and Python.
Build phases prefer `/usr/bin` over runtime-manager shims, including in child
processes. From the repository root:

```sh
just test opencode-local
# Inspect the successful run's archive, then install it on an Arch host:
sudo pacman -U /absolute/path/to/opencode-local-2.2.7-4-x86_64.pkg.tar.zst
opencode
```

The test command builds and validates in disposable containers without host
installation; do not run it with sudo. The explicit pacman transaction retains
normal prompts. The recipe also works directly with makepkg.
It provides `opencode=2.2.7`, satisfies OpenChamber's OpenCode requirements, and
conflicts with other packages owning the same executable.

The package uses upstream's standard `latest` release channel, not a separate
local/development channel. The `-local` suffix identifies the Arch package only;
normal sessions and credentials remain in `~/.local/share/opencode/opencode.db`
(subject to XDG and upstream database overrides). Earlier local-channel builds
used `opencode-local.db`; that database is left untouched and is not merged into
the standard database. Accounts or sessions created only there are not available
in the standard database; reconnect missing accounts with `/connect`.

## System integration

- Upstream's `opencode --completions <shell>` generates Bash, Zsh, and Fish
  completions during `build()`, installed in Arch's standard completion
  directories. No shell startup file
  is changed. Zsh and Fish are needed only for installed-package validation,
  not to build or use the package.
- No man page is shipped: this upstream release provides neither a manual nor
  a man-page generator. Use `opencode --help` and the upstream README installed
  in `/usr/share/doc/opencode-local/`. Recheck upstream integration support when
  updating versions; missing manuals/completions are not maintained downstream.
- No desktop entry, icon, MIME handler, or system service is shipped for this
  CLI/server package. Application background-service management remains an
  explicit user operation; installation does not enable services.

## Update and privacy policy

`prepare.py` verifies complete SHA-256 hashes of the reviewed upstream modules,
lockfile, and build scripts before editing anything. Unknown or partially
patched inputs fail rather than receiving partial changes.

| Boundary | Packaged behavior |
| --- | --- |
| Application updater | No remote version lookup, background checks, notifications, downloads, or update execution; manual `upgrade`/`update` and `uninstall` refuse with pacman guidance |
| Observability and OTLP | Local console/file logging only; remote exporters, trace SDK construction, and exporter tracking IDs are removed, not merely disabled by defaults |
| Plugin updates | Automatic checks stop before registry/cache access; explicit management of user-installed plugins remains available |
| Ripgrep and formatters | Ripgrep uses `/usr/bin/rg`; Prettier, oxfmt, and Biome resolve installed executables instead of downloading them |
| Claude Agent children | Reporting/updater-disable flags override inherited environment and connection settings as defense in depth |
| Browser/desktop reporting | These runtimes and their Sentry integrations are not built |

Ordinary provider requests, authentication, MCP, pairing, and model-catalog
refreshes remain available. Explicit provider/plugin installation can download
code into user-owned caches; upstream's npm installation disables lifecycle
scripts and audit reports. These are not pacman-owned application files.
Plugins, custom tools, MCP processes, and separately installed programs can
make their own network requests. Environment flags do not control arbitrary
external code. Local logs and token/performance statistics remain available.

Source and implementation tests do not establish complete network isolation.
The full native/transitive dependency closure and all child-process reporting
behavior have not been independently audited.

## Build trade-offs and version adjustments

- Dependency fetching needs network access and uses the pinned Bun lockfile with
  `--frozen-lockfile --ignore-scripts`. The reviewed native-terminal helper
  permission repair is invoked explicitly. Builds skip implicit installs,
  replacement Bun downloads, and the web UI, and use the committed model catalog.
- The executable embeds the installed Arch Bun runtime and locked native
  dependencies. Updating system Bun alone does not update the embedded runtime;
  rebuild this package. CPU requirements follow that Bun build, not merely the
  `linux-x64` target name. This is not a full native-dependency source rebuild
  or a verified offline/reproducible build.
- Stripping is disabled to preserve Bun's appended executable payload. Split
  debug packaging is disabled to keep every package name ending in `-local`.
  Default LTO remains enabled; application linker flags cannot relink Bun.
- Arch Bun 1.4.4 lacks PIE and FULL RELRO. This upstream-runtime limitation is
  accepted instead of maintaining a separate Bun toolchain. `namcap.toml` pins
  six exact archive warnings to this package revision and the audited Bun build:
  those two hardening findings, the ELF loader, gcc-libs, ripgrep, and Git.
  New, changed, duplicated, or disappeared findings require review.
- Upstream has stale patch declarations for `@standard-community/standard-openapi`
  0.2.9, `@ai-sdk/mistral` 3.0.51, and `@ai-sdk/google` 3.0.73, which are not
  installed, and for `gcp-metadata` 8.1.2, while 8.1.4 is locked. The last patch
  suppresses a non-GCP discovery warning, not telemetry. Foreign-platform native
  warnings and ast-grep's skipped lifecycle-script warning are also expected;
  ast-grep must still run using its locked native binary. Do not ignore new
  patch failures or enable lifecycle scripts to silence warnings.
- Embedded build-root strings trigger makepkg's `$srcdir` warning. Relocated
  executable smoke tests cover common CLI/API paths, not every bundled path.

For a version update, re-audit policy boundaries, input hashes, lockfile and
build commands, native dependencies, upstream patch warnings, and archive-lint
exceptions. Do not carry exceptions forward without checking them.

## Validation scope

`just check` runs repository and recipe regression checks; `just verify` checks
sources. Makepkg's `check()` runs policy/upstream tests, reviewed-module lint and
format checks, CLI TypeScript checks, and relocated CLI/API smoke tests, including
the standard release database path without database overrides.

`just test opencode-local` builds and installs in disposable containers.
Installed checks cover completion syntax and behavior, CLI/API refusals,
preserved user state, and TUI prompt/version rendering. Zsh and Fish are declared
in `validation.toml`; missing tools fail validation rather than being skipped.

Containers do not replace a clean Arch chroot. Release validation still requires
a successful final-source build, archive lint, installed checks, a clean-chroot
build, and fuller interactive/provider/network testing. The complete upstream
desktop/browser/cloud suite is outside this recipe's test scope; the package is
not yet release-ready.
