# openchamber-web-local

Source build of [OpenChamber](https://github.com/openchamber/openchamber)
**2.7.0** for **x86_64**, providing the web UI/server and `openchamber` CLI.
No Electron desktop client or bundled OpenCode executable is installed.
Pacman manages application updates; installation does not rewrite user settings
or enable services.

## Requirements and use

Runtime dependencies are Node.js >=24.14, OpenCode >=2.2.4 and <3, ripgrep, Git,
glibc, and gcc-libs. Local dictation model extraction optionally needs `tar` and
`bzip2`. Building requires Arch's base-devel toolchain, Bun >=1.4, Python,
and node-gyp. Build phases prefer `/usr/bin` over runtime-manager shims,
including in child processes.

From the repository root:

```sh
just install opencode-local
just install openchamber-web-local
openchamber --ui-password 'choose-a-password'
```

Install commands resolve dependencies and use normal pacman prompts; do not run
them with sudo. The recipe also works directly with makepkg. A compatible
distribution OpenCode package satisfies the dependency, but does not necessarily
provide this repository's downstream update/privacy policy.

The server binds to localhost by default. Do not expose it to an untrusted
network without authentication, and do not run it as root. An optional user
service is shipped, disabled by default:

```sh
systemctl --user enable --now openchamber-web.service
```

Optional service environment belongs in `~/.config/openchamber/service.env`
(mode 0600), for example `OPENCHAMBER_UI_PASSWORD=...`. The package never writes
this file. Prefer the packaged user unit over the application's startup installer.

## System integration

- No man page is shipped: this upstream release provides neither a manual nor
  a man-page generator. Use `openchamber --help` and the upstream web-package
  README installed in `/usr/share/doc/openchamber-web-local/`. Recheck upstream
  integration support when updating versions; missing manuals/completions are
  not maintained downstream.
- Upstream's `openchamber tunnel completion <shell>` generates Bash, Zsh, and Fish
  completions during `build()`, installed in Arch's standard completion
  directories. They cover top-level
  commands and tunnel/profile options, not every subcommand. Some descriptions
  still mention updates; those commands refuse with pacman guidance.
  Zsh and Fish are needed only for installed-package validation, not for building
  or running the application. No shell startup files are changed.
- There is no desktop entry, icon, or MIME association for this browser-served
  application. PWA service-worker generation and registration are disabled;
  offline/PWA push functionality is unavailable.

If reusing an origin previously served by upstream, manually unregister its old
service worker and reload/clear the old application cache. The package does not
delete browser state; existing tabs run their old JavaScript until reloaded.

## Update and privacy policy

`prepare.py` checks the complete SHA-256 of every affected upstream file before
making any changes. Unknown releases or already patched trees fail rather than
receiving partial source transformations.

| Boundary | Packaged behavior |
| --- | --- |
| Package manager and update routes | Local version/ownership information only; no remote lookup, usage payload, installation ID, npm probe, or update execution |
| CLI update and HTTP installers | CLI refuses before instance discovery/shutdown; update/install endpoints return HTTP 409 with pacman guidance, without downloads, restarts, or file replacement |
| OpenCode installation/upgrades | Installers refuse; upgrade status is local and compatibility reports no installer capability |
| Browser updater and notices | No update polling, tracking IDs, timers, downloads, or automatic update prompts for the application, OpenCode, or extensions |
| Usage settings | Reporting implementation is removed; defaults, setter, and server sanitizer reject reporting even with an existing saved opt-in |
| PWA | No generated or registered service worker that can replace the UI in the background |
| Child processes | Launcher enforces reporting/updater-disable environment flags as defense in depth |

The reviewed usage sender was the update check at
`api.openchamber.dev/v1/update/check`, carrying installation IDs and device/runtime
metadata. Its implementation is removed, not merely configured to omit an ID.
Source inspection found no separate Sentry/PostHog/crash-reporting sender in the
examined application code. Local token/cost/performance statistics remain.

This is not an offline application or a guarantee of zero outbound traffic.
Providers, Git hosts, model catalogs, notifications, remote access, agents, and
explicit integrations still use the network. Explicit management of user-owned
extensions remains available; extension code can implement its own updates or
reporting. Separately installed OpenCode, browser runtimes, plugins, and the full
dependency closure remain outside the package's complete control. Source tests
and a short browser startup capture do not establish full network isolation.

## Build trade-offs and version adjustments

- Dependency fetching needs network access and uses the pinned Bun lockfile with
  `--frozen-lockfile --ignore-scripts`. SDK/web builds and native-terminal addon
  compilation are invoked explicitly; node-gyp uses `/usr` headers without
  downloading replacements. React Scan injection is disabled.
- Deployment retains production dependencies, required peers, nested versions,
  licenses, and internal relative links, not the monorepo's entire node_modules.
  Known native packages are trimmed to Linux x86_64/glibc. The locally compiled
  node-pty addon replaces its fallback prebuilds/build tools. Some other locked
  dependencies still contain upstream native binaries: this is not a complete
  native-dependency source rebuild or a verified offline/reproducible build.
- Linux dictation uses system tar/bzip2; the Windows-only `7zip-bin` dependency
  is omitted. Revisit this exclusion if upstream's model downloader changes.
- Default stripping and LTO remain enabled. Split debug packaging is disabled
  to keep every package name ending in `-local`.
- Type checks invoke the pinned `tsc -b` directly because upstream's `bun check`
  scripts are unsupported by the audited Arch Bun 1.4.4. `lint.py` passes explicit
  source files to oxlint to avoid inherited packaging ignore rules, and rejects
  empty or incomplete scans. Four existing `exhaustive-deps` warnings in
  `OfficeArtifact.tsx`, `useDraftStarters.ts`, `useTextareaInlinePicker.tsx`, and
  `PluginPane.tsx` have exact baselines; new, changed, duplicated, or disappeared
  findings require review. Vite's upstream `::highlight` optimizer and bundle-size
  warnings remain known build diagnostics.

For a version update, re-audit source hashes and policy boundaries, frontend
reporting/update paths, dependency deployment/exclusions, native addons, upstream
build commands, lint exceptions, and completion coverage.

## Validation scope

`just check` runs repository and recipe regression checks; `just verify` checks
sources. Makepkg's `check()` runs web/UI lint and type checks, policy tests,
focused upstream ripgrep/health-wait/model-downloader tests, and staged CLI/server/
native-addon checks. These also check web assets and the absence of a shipped
service worker or Electron runtime. The full desktop/mobile/VS Code/remote-service
suite is outside this web-only recipe's scope.

`just container-test openchamber-web-local` builds and installs the patched
`opencode-local` dependency before OpenChamber. Installed tests cover CLI refusals,
native-terminal behavior, authenticated HTTP/UI delivery, an existing reporting
opt-in, local update status, installer refusals, completion syntax/behavior, and
unit-file verification. Zsh, Fish, and systemd are declared only in
`validation.toml`; missing tools fail validation rather than being skipped.
Unit verification is not a real
user-manager lifecycle test.

Containers do not replace a clean Arch chroot. The generic `just chroot` uses the
distribution OpenCode provider; to test the patched dependency, pass its archive
to devtools from this recipe directory:

```sh
extra-x86_64-build -- -I /absolute/path/to/opencode-local.pkg.tar.zst
```

Release validation still requires a successful final-source build, archive lint,
installed checks, a clean-chroot build, installed-browser/network testing, and
real user-service start/stop/restart checks. The package is not yet release-ready.
