# @PACKAGE@

This recipe is unfinished. Replace every TODO before validation or distribution.
The layout follows Arch's `/usr/share/pacman/PKGBUILD.proto`; the recipe must
remain independently buildable with makepkg.

## Contents and usage

TODO: describe the packaged upstream components, commands, runtime dependencies,
configuration paths, and any administrator-controlled service setup.

## Update and privacy policy

TODO: audit updater and reporting implementations, including dependencies and
child processes. Explain enforcement, manual-update refusal, ordinary networking,
external boundaries, regression tests, and remaining limitations.

After pinning sources and generating metadata, run `just discover @PACKAGE@` for
advisory file/line leads in verified, unpatched sources. Review the report's
questions, coverage, and exclusions; a no-match result is not a compliance verdict.
Prefer supported upstream switches before implementation-boundary patches. Re-run
discovery on version updates; keep review conclusions and tests here, and detailed
run evidence in artifacts.

## System integration

TODO: assess upstream-provided Bash, Zsh, and Fish completions, man pages, useful
documentation, and applicable service/XDG integration. Document omissions; do not
invent missing upstream integrations or enable services automatically.

## Building and validation

TODO: pin sources and dependency lockfiles, verify strong checksums, classify
dependencies, review necessary lifecycle steps, and document fetching/network
requirements. Keep all recipe-specific fixtures and tests here.

Generate metadata with `just srcinfo @PACKAGE@`. Run `just check @PACKAGE@`,
`just verify @PACKAGE@`, and `just test @PACKAGE@`. The installed test deliberately
fails until implemented. Declare local providers and extra installed-test tools
in an optional `validation.toml`; do not add a shared package registry.

TODO: document actual upstream checks, archive/runtime assertions, genuine clean
Arch chroot validation, integration-specific release gates, and remaining gaps.
Keep detailed execution evidence in validation artifacts, not this README.
