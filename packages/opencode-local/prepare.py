"""Apply the reviewed OpenCode 2.2.7 pacman/privacy policy, rejecting upstream drift."""

import argparse
import hashlib
from pathlib import Path

EXPECTED = {
    "packages/cli/src/services/updater.ts": "6cba0e82daf5451151dd821de6f37e4146db4555159f9e61baac745b359c3b33",
    "packages/cli/src/services/updater.test.ts": "84b5e645b48cb26fe521ff7ab71368a1e3996878f36604e4571b19e2303dbebf",
    "packages/cli/src/commands/handlers/upgrade.ts": "723589b3d89e3ff0c3e99ed545cb975890ec51621888fa0f15929e3be9e6e118",
    "packages/cli/src/commands/handlers/uninstall.ts": "8d742629be1015fea121dfe8962988ec9ec36b86d882d5ecede1b10edb7cea9c",
    "packages/util/src/observability/otlp.ts": "6b455d12c35b6f99c3b7f53b4764b52df1434abf1b7fefc3bb2ad87af2ba34e8",
    "packages/util/src/observability.ts": "2055d5125da4ca7de3cc78871fbff3f3e8ccc9a582824d838021c60962b33eea",
    "packages/core/src/plugin/update.ts": "1c23698f8b51a84cb78e1e5d3b53d4d09bd260789cc52de449dff81e84344593",
    "packages/core/src/ripgrep/binary.ts": "cb0866ec835b78eb18eb783198b1959a82469fbd7bfd1f21793d743310831755",
    "packages/core/src/formatter/builtins.ts": "41f092b5203934645ddbd7e116353afc313dde098ece5f4b858ea78246f112e2",
    "packages/core/src/plugin/provider/claude-agent/claude/query-options.ts": "b82441c823097f44352ec134e2622cd1828292a0bcc299728acfdfebe501eba9",
    "bun.lock": "1cdfae839e296cdef87ec67e84d7ac716b303e685e3281ab383a1d29493976c9",
    "packages/cli/script/build.ts": "e8510bef1246c68b3549126db647e5ba3f1ba2345d23d7627bb9e38f0b5c8a8b",
    "packages/core/script/fix-node-pty.ts": "f5488e74cf12b343852847769d85860f8e24ee2bbb3e34e62deb4fef1a0576ad",
}

MESSAGE = "OpenCode is managed by pacman. Rebuild opencode-local and upgrade with pacman -U."


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one policy boundary: {old!r}")
    return text.replace(old, new, 1)


def prepare(root):
    texts = {}
    # Validate every input before writing any of them, including the untouched
    # lockfile and build scripts whose behavior the recipe relies on.
    for name, expected in EXPECTED.items():
        data = (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Unreviewed upstream source: {name}")
        texts[name] = data.decode()

    name = "packages/cli/src/services/updater.ts"
    original = texts[name]
    # Retain upstream's public contracts and error type, not its installer,
    # version discovery, filesystem operations, or package-manager subprocesses.
    contracts = original[
        original.index("export const methods") : original.index("const decodeVpPackages")
    ]
    interface = original[
        original.index("export interface Interface") : original.index(
            "export function decodePolicy"
        )
    ]
    texts[name] = (
        'import { Context, Effect, Layer } from "effect"\n\n'
        + contracts
        + interface
        + f'export const managedMessage = "{MESSAGE}"\n'
        + """
const refused = () => Effect.fail(new Error(managedMessage))
export function decodePolicy(_text: string): "disable" {
  return "disable"
}
export const layer = Layer.succeed(
  Service,
  Service.of({
    run: () => Effect.succeed(undefined),
    check: () => Effect.succeed({ type: "unavailable", message: managedMessage }),
    apply: refused,
    method: () => Effect.succeed(undefined),
    latest: refused,
    upgrade: refused,
    removal: () => undefined,
  }),
)
export * as Updater from "./updater"
export { action, type Action, type Policy } from "./updater-action"
"""
    )
    for command in ("upgrade", "uninstall"):
        texts[
            f"packages/cli/src/commands/handlers/{command}.ts"
        ] = f"""import {{ Effect }} from "effect"
import {{ Commands }} from "../commands"
import {{ Runtime }} from "../../framework/runtime"
import {{ managedMessage }} from "../../services/updater"

export default Runtime.handler(
  Commands.commands.{command},
  Effect.fn("cli.{command}")(function* () {{
    return yield* Effect.fail(new Error(managedMessage))
  }}),
)
"""

    name = "packages/cli/src/services/updater.test.ts"
    start = texts[name].index('  test("reads update policy from JSONC"')
    end = texts[name].index('  test("reports every available release"')
    texts[name] = (
        texts[name][:start]
        + """  test.each([
    '{ // preference\\n "update": "notify",\\n}',
    '{ "update": "disable" }',
    '{ "update": "auto" }',
    '{ "update": "invalid" }',
    '{ "autoupdate": false }',
    '{ "autoupdate": "notify" }',
    '{ "autoupdate": true }',
  ])("pacman policy cannot be overridden: %s", (text) => {
    expect(decodePolicy(text)).toBe("disable")
  })

"""
        + texts[name][end:]
    )

    name = "packages/cli/script/build.ts"
    texts[name] = replace_once(texts[name], 'import { createHash } from "node:crypto"\n', "")
    texts[name] = replace_once(
        texts[name], 'import { mkdir, rm } from "fs/promises"', 'import { rm } from "fs/promises"'
    )
    texts[name] = replace_once(
        texts[name], "const releaseAssets = new Map<string, Promise<Map<string, string>>>()\n", ""
    )
    start = texts[name].index("async function compileExecutable(")
    end = texts[name].index("function targetName(")
    texts[name] = (
        texts[name][:start]
        + """// Use the installed build dependency, never Bun's implicit target-runtime download.
async function compileExecutable(_item: (typeof allTargets)[number]) {
  return process.execPath
}

"""
        + texts[name][end:]
    )

    texts["packages/util/src/observability/otlp.ts"] = """import { Effect, Layer } from "effect"

export interface Options {
  readonly endpoint?: string
  readonly headers?: string
}
export interface App {
  readonly client: string
  readonly version: string
  readonly channel: string
}
// Reporting cannot be re-enabled by environment, configuration, or SDK options.
export function loggers(_options: Options | undefined, _app: App) {
  return []
}
export const tracingLayer = (_options: Options | undefined, _app: App) => Effect.succeed(Layer.empty)
export * as Otlp from "./otlp.js"
"""
    texts[
        "packages/util/src/observability.ts"
    ] = """export * as Observability from "./observability.js"

import { NodeFileSystem } from "@effect/platform-node"
import { LayerNode } from "./effect/layer-node.js"
import { Layer, Logger, References, Schema } from "effect"
import { Logging } from "./observability/logging.js"

export const Options = Schema.Struct({
  endpoint: Schema.optional(Schema.String),
  headers: Schema.optional(Schema.String),
  client: Schema.optional(Schema.String),
  version: Schema.optional(Schema.String),
  channel: Schema.optional(Schema.String),
})
export type Options = typeof Options.Type

// Keep local console/file diagnostics; do not construct remote exporters.
export function layer(options: Options = {}) {
  const channel = options.channel ?? "local"
  return Logger.layer(Logging.loggers(channel === "local", channel), { mergeWithExisting: false }).pipe(
    Layer.provide(NodeFileSystem.layer),
    Layer.orDie,
    Layer.merge(Layer.succeed(References.MinimumLogLevel, Logging.minimumLogLevel())),
  )
}
export const node = LayerNode.make({
  name: "observability",
  layer: Layer.suspend(() => layer()),
  deps: [],
})
"""

    name = "packages/core/src/plugin/update.ts"
    texts[name] = replace_once(
        texts[name],
        "check: (target, options) =>\n        locks.withLock(target)(",
        "// User-installed plugins may be checked explicitly, never in the background.\n"
        "      check: (target, options) => {\n"
        "        if (!options?.refresh) return Effect.succeed(false)\n"
        "        return locks.withLock(target)(",
    )
    texts[name] = replace_once(
        texts[name],
        "        ),\n      // Serialize per target",
        "        )\n      },\n      // Serialize per target",
    )
    texts[
        "packages/core/src/ripgrep/binary.ts"
    ] = """import { Context, Effect, Layer } from "effect"
import { makeGlobalNode } from "@opencode/util/effect/app-node"
import { FSUtil } from "@opencode/util/fs-util"

export namespace RipgrepBinary {
  interface Interface {
    readonly filepath: Effect.Effect<string, Error>
  }
  export class Service extends Context.Service<Service, Interface>()("@opencode/RipgrepBinary") {}
  const layer = Layer.effect(
    Service,
    Effect.gen(function* () {
      const fs = yield* FSUtil.Service
      return Service.of({
        filepath: Effect.gen(function* () {
          if (yield* fs.isFile("/usr/bin/rg").pipe(Effect.orDie)) return "/usr/bin/rg"
          return yield* Effect.fail(new Error("Install ripgrep with pacman; automatic downloads are disabled."))
        }),
      })
    }),
  )
  export const node = makeGlobalNode({ service: Service, layer, deps: [FSUtil.node] })
}
"""
    name = "packages/core/src/formatter/builtins.ts"
    for package, binary in (
        ("prettier", "prettier"),
        ("oxfmt", "oxfmt"),
        ("@biomejs/biome", "biome"),
    ):
        texts[name] = replace_once(
            texts[name], f'yield* input.npm.which("{package}")', f'findExecutable("{binary}")'
        )
    name = "packages/core/src/plugin/provider/claude-agent/claude/query-options.ts"
    texts[name] = replace_once(
        texts[name],
        '  CLAUDE_CODE_DISABLE_CLAUDE_MDS: "1",',
        '  DO_NOT_TRACK: "1",\n'
        '  DISABLE_TELEMETRY: "1",\n'
        '  DISABLE_ERROR_REPORTING: "1",\n'
        '  DISABLE_AUTOUPDATER: "1",\n'
        '  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1",\n'
        '  CLAUDE_CODE_DISABLE_CLAUDE_MDS: "1",',
    )
    for name, text in texts.items():
        if text != (root / name).read_text():
            (root / name).write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    prepare(parser.parse_args().root)
