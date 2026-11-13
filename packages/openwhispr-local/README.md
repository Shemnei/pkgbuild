# OpenWhispr local

Source-built OpenWhispr desktop application for x86_64 Linux. Run `openwhispr`
from a desktop session with a working microphone and PulseAudio/PipeWire audio.
Electron 43 and FFmpeg are provided by Arch, not downloaded by the application.
This is the desktop application, not the separate mobile client or cloud backend.

## Integrations

- Application menu entry, upstream icon, and `openwhispr://` URL-handler metadata.
  Choose the URL handler in your desktop settings if needed. Startup never claims
  MIME defaults or writes a competing desktop entry.
- Upstream's Linux key listener, X11/uinput/portal paste helper, AT-SPI text
  monitor, and PipeWire system-audio helper are compiled with Arch build flags.
- Optional clipboard, keystroke, active-window, and media-control programs are
  listed in `optdepends`. Wayland support depends on the compositor, portals,
  permissions and selected backend; do not grant broad input-device access just
  to silence a diagnostic. Configure optional daemons yourself.
  Automatic ydotool service startup and permission-change prompts are removed;
  read-only integration-status checks remain available.
- User-requested autostart uses `/usr/bin/openwhispr`, not the Electron executable.
  Installation never starts the app, enables services, or changes user settings.
- No systemd service is appropriate for this interactive desktop app. Upstream
  provides no man-page or Bash/Zsh/Fish completion file or supported generator;
  none is synthesized. Proprietary brand-font downloads are omitted; upstream's
  bundled/open-source fallback fonts remain available.

## Software ownership and local engines

The updater implementation is replaced: startup/periodic checks do nothing,
status reports unsupported, and manual check/download/install calls direct the
user to pacman. Persisted preferences and AppImage environment variables cannot
restore the updater. GPU-pack installers and their migration/removal paths are
disabled; their direct download calls fail with pacman guidance.

URL audio import uses only `/usr/bin/yt-dlp`. Its cache seeding, background/nightly
updater and forced self-heal update are removed. Calls ignore user yt-dlp config,
disable updates and remote executable components, and still allow requested media
downloads. Update yt-dlp through pacman if an extractor becomes incompatible.

Local Whisper uses the package's CPU-only server, built from the same pinned
Whisper fork release as upstream and installed privately under
`/usr/lib/openwhispr/resources/bin/`. No separately downloaded executable is used.
Select a Whisper model in the app; speech models are user data, not bundled
software. The build disables host-specific CPU instructions and retains Arch
compiler/linker flags. GPU-pack selection is unavailable.

Optional Llama support resolves only `/usr/bin/llama-server`. Manual Llama
install/removal controls refuse with pacman guidance rather than creating or
deleting user-local executables. Install a suitable pacman-managed provider
separately. Its fork/version-dependent arguments and acceleration configuration
remain outside the bundled Whisper engine's validation scope.
Sherpa/Parakeet executables, Qdrant, meeting AEC, and pretrained embedding/VAD/
diarization models are not bundled. Features requiring missing helpers are
unavailable; BYOK dictation and keyword note search remain available.
User-requested model-data downloads remain supported and require network access.

The public source release leaves the OpenWhispr Cloud API URL empty; upstream
injects it through a repository variable during its release builds. This recipe
does not guess that endpoint. Hosted Cloud transcription therefore needs an
operator-supplied `VITE_OPENWHISPR_API_URL` at build time, which upstream embeds in
both renderer assets and `src/dist/runtime-env.json`. Account authentication
defaults to upstream's `https://auth.openwhispr.com`; changing authentication
servers also requires setting `VITE_AUTH_URL` when building. These are service
addresses, never credentials. The normal local Whisper and direct-provider BYOK
paths do not require the managed Cloud endpoint. Account/provider compatibility
still requires live validation.

## Privacy policy and audit boundaries

- Desktop main/preload, renderer settings and services, IPC serialization,
  background jobs, runtime downloaders and child-process launch paths were
  inspected. The mobile tree is not built or shipped.
- Remote usage-event/participation uploads and analytics reads are rejected at
  the backend Cloud API request boundary, even if persisted preferences request
  them. Remote analytics erasure (`DELETE`) remains available. Local Insights
  recording and summaries remain intact; Insights sync and telemetry settings
  cannot enable reporting.
- Cloud dictation/reasoning serialization omits diagnostic session IDs, timing,
  language-routing comparisons and analytics-only fields; `sendLogs` is always
  false. Content, authentication, policy/version headers, transcription sync IDs
  and service quota/billing receipts are functional protocol data and remain.
- Both Node and browser exports of the lockfile-pinned Better Auth telemetry
  package are replaced before bundling and in the production dependency tree.
  Options, environment variables and custom tracking callbacks cannot re-enable
  those exporters. OpenTelemetry API dependencies alone do not export reports.
- No desktop Sentry/PostHog/crash-report exporter was found. This source audit is
  not a claim of network isolation. Cloud/BYOK requests, authentication, account
  sync, connectors and requested model/media downloads still use the network;
  their servers and separately installed programs remain outside package control.
  Some upstream controls may remain visible even where the backend refuses them.

## Build and validation

The release tarball and Electron headers have SHA-256 checksums. Upstream's npm
lockfile pins dependency versions and integrity. `npm ci --ignore-scripts` fetches
the build and production graphs; no lifecycle script or upstream prebuild/download
script runs. Only the renderer build, local C/C++ compilation, and better-sqlite3
node-gyp builds are invoked. Keyring/ONNX Node-API artifacts supplied by the
integrity-pinned npm dependencies are used; there is no install-time downloader.
Network is needed for source/dependency fetches; offline or reproducible builds
have not been established. Native SQLite is rebuilt for system Node tests and
the Electron 43 ABI separately, linking Arch's system SQLite rather than the
npm dependency's bundled database library.

The CPU Whisper source is separately checksummed. A commit-pinned, checksummed
quantized Tiny English model is fetched solely as a build-test fixture and is
never installed. The server is exercised with upstream's speech sample over
loopback; the upstream UTF-8 helper unit test also runs. Optional VAD model data
is not bundled: upstream warns and transcribes without VAD when it is absent.

The production manifest/lock root omits upstream's `shadcn-ui` scaffolding CLI
and the unused `electron-updater`/`ffmpeg-static` downloaders. Other locked
resolutions are unchanged; dependencies are not upgraded or re-resolved for
staging. The Qdrant client's type-check-only TypeScript peer and npm CLI links
are removed from the final tree, matching upstream's compiler exclusion. Native
addon sources/installers, foreign-language fixtures and dependency test trees
are omitted. Private JavaScript modules have no standalone Node shebangs or npm
script/bin entry points; they run through system Electron. ONNX's foreign-platform
binaries are also removed. Dependency license/notice files are additionally copied
under `/usr/share/licenses/openwhispr-local/node_modules/`.

The application is MIT-licensed; bundled libraries have additional licenses,
including AGPL-3.0-or-later for Tinfoil and OFL-1.1 for fonts. npm-supplied license
files remain beside their libraries. ONNX's npm archive omits its license/notices,
so matching versioned upstream documents are separately checksummed and installed
in `/usr/share/licenses/openwhispr-local/`. Review third-party source and notice
obligations before redistributing built archives; this recipe is not a substitute
for that redistribution audit.

`prepare.py` verifies complete input-file hashes before applying changes, and
`dependencies.py` checks both dependency-exporter hashes. Version updates require
re-auditing those boundaries, upstream dependency scripts, engine compatibility,
integrations and any newly introduced exporters/installers, not merely updating
checksums. The stage uses upstream's runtime layout without Electron/build tools.

`check()` includes upstream lint/format/type checks, focused upstream audio/text/
local-statistics tests, CPU engine unit/transcription tests, policy tests on patched
and staged code, desktop validation,
and native SQLite loading under Electron. The full upstream suite is not yet a
gate: updater/cache/GPU/MIME-default tests assert intentionally replaced contracts;
the focused selection does not establish coverage of all other application paths.
`tests/installed.py` checks installed contents, native addons, the real launcher,
local React page, preload and updater IPC under Xvfb/D-Bus without disabling the
sandbox. It also validates the user-requested autostart desktop entry and its
removal inside an isolated temporary home. It does not validate real microphone
capture, compositor paste/hotkeys, system audio, account sign-in, provider traffic,
or autostart across a real login. Chromium needs working unprivileged user/PID/
network namespaces to retain its sandbox in a no-new-privileges container. An
engine that denies those namespaces blocks GUI validation; use a compatible
engine/host or VM rather than sandbox-disable flags or privileged containers.

The pinned upstream lint reports four unused destructured variables in
`settingsStoreMeetingFollowFlags.test.js` and two Fast Refresh export warnings in
`components/icons/primitives.tsx`. These concern test/development code, not the
production renderer; the standard lint gate still rejects errors. Compiler
warnings in pinned Node/V8 headers and SQLite's addon conversion code are upstream
limitations; normal hardening flags remain enabled.

`namcap.toml` records exact version/toolchain-pinned archive warnings: shared
Node-API modules are not PIE executables, upstream native artifacts retain older
glibc compatibility links, and subprocess/dynamically loaded integrations escape
ELF-only dependency detection. The prebuilt ONNX binding lacks FULL RELRO; this is
an accepted upstream hardening limitation, not a claim that it is fully hardened.
Revisit the source-build option or upstream artifact when updating ONNX. New,
changed or stale findings still fail validation, and no archive errors are waived.

Required release gates: `just check openwhispr-local`, `just verify openwhispr-local`,
`just test openwhispr-local`, a genuine clean Arch chroot build, interactive X11/
Wayland audio and paste tests, and live provider/privacy validation. Missing tools,
sandbox failures and untested engine compatibility are failures/gaps, not passes.
Detailed command evidence belongs in validation artifacts, not this README.
