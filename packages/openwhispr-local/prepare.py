"""Apply reviewed policy changes to exactly the audited upstream release."""

import hashlib
import re
import sys
from pathlib import Path

INPUTS = {
    "src/helpers/ensureYdotool.js": "f5580cec9e39e6ab6456c8fc2c6a500a43a270dd1463e05131d58bab43d10032",
    "main.js": "38b50456460bb1dbbc7398479bd6aff56359f20c9450e6e4909dc245c0bafc96",
    "src/updater.js": "f8e07b8c9fb71f66a98407f20cd83a7ed3977fea909b663f61cc246c32c507ae",
    "src/helpers/gpuBinaryManager.js": "091684ea72aa4269e663c6ea83cde0e5a6190462286ef0e5ee990bf57dfeb313",
    "src/helpers/urlAudioDownloader.js": "25ec76de1ab61cd3b75ef6d21e7dc9ba9ad70705759d3d883ea7c7b93d76df7d",
    "src/helpers/cloudApiRequest.js": "5ebd0d02c7f5427ca530e83b065778c1b8bf9319b7b9c968683ee99a4dbceb47",
    "src/helpers/ipcHandlers.js": "820c5ece28e2c5c23f3e3a291ae4fd73ac898b36c6ba2efddf4e650f61c08122",
    "src/helpers/linuxAutostart.js": "de52a89011afd7c01938b0ea766025473fdaa2d3ef607271eb508d2ca1a0c324",
    "src/helpers/linuxUrlSchemeHandler.js": "a520a0dee81a6c01ee86f1c0886607bbd1cdcdcda1d81bb7f6592b618771620a",
    "src/helpers/whisperServer.js": "6bbfe68e875ac1bf18ed49286e3895b59f8fc6abf5ccd8a1c7f4204fd16981ad",
    "src/helpers/llamaServer.js": "06597344e0f451af41c2d43dbb0ce914da5c68e7ffdaf9b4f8a960104346cd60",
    "src/helpers/llamaCppInstaller.js": "bb8c971baecc842735b5ec609a98607704d87b923dfc4dff946a4345bfaa62f4",
    "src/stores/settingsStore.ts": "af115bef5077db8401ac6f8f66085b8c3fcd2d9d032354d6a1d1fcd1b39ff932",
}


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f"Upstream boundary changed: {old!r}")
    return text.replace(old, new)


def region(text, start, end, replacement):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"Upstream region changed: {start!r}")
    begin, finish = text.index(start), text.index(end)
    if finish <= begin:
        raise ValueError("Reversed source region")
    return text[:begin] + replacement + text[finish:]


def prepare(root):
    # Validate every boundary before writing anything. A version bump requires a new audit.
    sources = {}
    for name, expected in INPUTS.items():
        data = (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Unaudited upstream input: {name}")
        sources[name] = data.decode()

    local = Path(__file__).resolve().parent
    sources["src/updater.js"] = (local / "updater.cjs").read_text()
    sources["src/helpers/gpuBinaryManager.js"] = (local / "gpu-binary.cjs").read_text()
    name = "src/helpers/ensureYdotool.js"
    sources[name] = region(
        sources[name],
        "async function ensureYdotool() {",
        "function isNixOS() {",
        "// Optional daemons and input permissions are administrator-managed.\n"
        "async function ensureYdotool() {}\n\n",
    )
    sources[name] = replace(sources[name], 'const { dialog } = require("electron");\n', "")
    sources[name] = region(
        sources[name], "function getLogger() {", "function commandExists(name) {", ""
    )
    sources["main.js"] = replace(
        sources["main.js"],
        'const path = require("path");',
        'const path = require("path");\n'
        "// Arch ships the application separately from system Electron.\n"
        'Object.defineProperty(process, "resourcesPath", { value: __dirname });\n'
        'app.setName("OpenWhispr");',
    )
    sources["main.js"] = replace(
        sources["main.js"],
        "function registerOpenWhisprProtocol() {",
        "function registerOpenWhisprProtocol() {\n"
        '  if (process.platform === "linux") return isOAuthSchemeRegistered();',
    )

    name = "src/helpers/urlAudioDownloader.js"
    text = sources[name]
    text = region(text, "// Writable yt-dlp cache,", "// Absolute ceilings", "")
    text = replace(text, 'const crypto = require("crypto");\n', "")
    text = replace(text, 'const os = require("os");\n', "")
    text = replace(text, "let ytDlpUpdateInFlight = false;\nlet ytDlpBusyCount = 0;\n", "")
    text = region(
        text,
        "function ytDlpBinaryName()",
        "// Resolve the OS/corporate proxy",
        "function resolveYtDlpBinary() {\n"
        '  return fs.existsSync("/usr/bin/yt-dlp") ? "/usr/bin/yt-dlp" : null;\n'
        "}\n\n"
        "async function maybeUpdateYtDlp() {\n"
        '  return { success: false, message: "Update yt-dlp with pacman." };\n'
        "}\n\n",
    )
    text = region(
        text,
        "// Single self-heal retry:",
        "// Startup sweep for orphans",
        "",
    )
    text = replace(text, "runYtDlpWithSelfHeal(", "runYtDlp(", count=2)
    text = replace(text, "      () => cleanupTempArtifacts(tempBase),\n", "")
    text = replace(text, "      resolveYtDlpBinary() || binary,", "      binary,")
    text = replace(
        text,
        "    // Stale-extractor failures usually surface here, so this step self-heals too.\n",
        "",
    )
    text = replace(
        text,
        "    // The metadata step may have self-healed the cache copy; pick up the fresh binary.\n",
        "",
    )
    text = replace(
        text,
        "    // Throttled background self-update for next time (never awaited).\n"
        "    maybeUpdateYtDlp();\n",
        "",
    )
    text = region(text, "// Heuristic: yt-dlp stderr/message", "// YouTube refusing", "")
    text = replace(text, "  seedYtDlpFromBundle();\n", "")
    text = replace(
        text,
        "  _isYtDlpUpdateInFlight: () => ytDlpUpdateInFlight,",
        "  _isYtDlpUpdateInFlight: () => false,",
    )
    text = replace(text, "  _recordCacheChecksum: recordCacheChecksum,\n", "")
    text = replace(
        text,
        "[...runtime.args, ...args]",
        '["--ignore-config", "--no-update", "--no-remote-components", ...runtime.args, ...args]',
    )
    sources[name] = text

    # Keep ordinary Cloud requests and remote erasure available, but never upload usage.
    name = "src/helpers/cloudApiRequest.js"
    sources[name] = replace(
        sources[name],
        "      // Concatenation (not targetUrl)",
        '      const route = decodeURIComponent(targetUrl.pathname).replace(/\\/+$/, "");\n'
        '      if ((route === "/api/analytics" || route.startsWith("/api/analytics/")) &&\n'
        '          (opts.method || "GET").toUpperCase() !== "DELETE") {\n'
        '        return { success: false, error: "Remote usage analytics are disabled by this package" };\n'
        "      }\n"
        "      // Concatenation (not targetUrl)",
    )

    # Remove diagnostic/usage-only metadata at the outbound serialization sites.
    # Preserve authentication, policy versions, content, quota receipts, and sync IDs.
    name = "src/helpers/ipcHandlers.js"
    text = sources[name]
    keys = (
        "sessionId",
        "localDate",
        "analyticsOccurredAt",
        "analyticsWordCount",
        "analyticsCounterVersion",
        "streamingFallbackReason",
        "sttProcessingMs",
        "sttWordCount",
        "sttDetectedLanguageConfidence",
        "sttDetectedLanguageAudioSeconds",
        "sttDetectedLanguageStatus",
        "orukeetLanguageRouting",
        "orukeetCleanup",
        "clientTotalMs",
    )
    # Only these three remote payloads; local recording and Insights remain intact.
    boundaries = (
        (
            "        const multipartFields = {",
            '\n        debugLogger.debug("Cloud transcribe request"',
        ),
        ("            requestPurpose: opts.requestPurpose,", "\n        if (!response.ok) {"),
        ("              audioDurationSeconds,", "\n          if (response.status === 401) {"),
    )
    for start, end in boundaries:
        begin = text.index(start)
        finish = text.index(end, begin)
        block = text[begin:finish]
        for key in keys:
            block = re.sub(
                rf"^\s*{key}: [^\n]*(?:\n\s+\? [^\n]*\n\s+: undefined,)?\n",
                "",
                block,
                flags=re.MULTILINE,
            )
        block = block.replace("sendLogs: opts.sendLogs,", "sendLogs: false,")
        text = text[:begin] + block + text[finish:]
    # The same per-launch tracking ID also accompanies file imports, discarded
    # audio retries, and agent streaming. It is not an authentication/sync ID.
    text = replace(text, "    this.sessionId = crypto.randomUUID();\n", "")
    text, removed = re.subn(r"^ +sessionId: this.sessionId,\n", "", text, flags=re.MULTILINE)
    if removed != 3:
        raise ValueError("Unexpected remaining diagnostic session-ID sites")
    text = replace(
        text,
        '                  source: "dictation",\n',
        '                  source: "dictation",\n                  sendLogs: false,\n',
    )
    text = replace(
        text,
        '            source: purpose === "dictation" ? "dictation" : "file_upload",\n',
        '            source: purpose === "dictation" ? "dictation" : "file_upload",\n'
        "            sendLogs: false,\n",
    )
    sources[name] = text

    name = "src/helpers/linuxAutostart.js"
    sources[name] = region(
        sources[name],
        "function resolveExecutablePath()",
        "function findBundledIconSource()",
        'function resolveExecutablePath() {\n  return "/usr/bin/openwhispr";\n}\n\n',
    ).replace('const WRAPPED_BINARY_SUFFIX = "-app";\n', "")

    name = "src/helpers/linuxUrlSchemeHandler.js"
    # System desktop metadata declares the scheme. Never claim a user's default at startup.
    sources[name] = (
        "function registerLinuxUrlSchemeHandler() {\n  return { registered: false };\n}\n\n"
        "module.exports = { registerLinuxUrlSchemeHandler };\n"
    )

    name = "src/helpers/whisperServer.js"
    sources[name] = region(
        sources[name],
        "  getServerBinaryPath(options = {}) {",
        "  isAvailable() {",
        "  getServerBinaryPath() {\n"
        '    const binary = path.join(__dirname, "..", "..", "resources", "bin", "whisper-server");\n'
        "    return fs.existsSync(binary) ? binary : null;\n"
        "  }\n\n",
    )
    sources[name] = replace(
        sources[name],
        'const { BIN_SUBDIR: CUDA_BIN_SUBDIR } = require("./whisperCudaManager");\n',
        "",
    )
    sources[name] = replace(
        sources[name],
        'const { BIN_SUBDIR: VULKAN_BIN_SUBDIR } = require("./whisperVulkanManager");\n',
        "",
    )
    sources[name] = replace(sources[name], 'const { app } = require("electron");\n', "")

    name = "src/helpers/llamaServer.js"
    sources[name] = region(
        sources[name],
        "  getServerBinaryPaths() {",
        "  isAvailable() {",
        "  getServerBinaryPaths() {\n"
        '    return fs.existsSync("/usr/bin/llama-server") ? { cpu: "/usr/bin/llama-server" } : {};\n'
        "  }\n\n",
    )
    sources[name] = re.sub(
        r'^const \{ BIN_SUBDIR: LLAMA_VULKAN_BIN_SUBDIR \} = require\("./llamaVulkanManager"\);\n',
        "",
        sources[name],
        flags=re.MULTILINE,
    )
    sources[name] = replace(sources[name], 'const { app } = require("electron");\n', "")

    # These are the four methods consumed by the installer-status IPC handlers.
    # Report the actual system binary, never create/remove a user-local install.
    sources["src/helpers/llamaCppInstaller.js"] = """const fs = require("fs");
const { spawnSync } = require("child_process");
const binary = "/usr/bin/llama-server";
function available() {
  try {
    fs.accessSync(binary, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}
const managed = () => ({ success: false, message: "Manage llama.cpp with pacman." });
module.exports = { default: {
  async isInstalled() { return available(); },
  async getVersion() {
    if (!available()) return null;
    const result = spawnSync(binary, ["--version"], { encoding: "utf8", timeout: 5000 });
    if (result.error || result.status !== 0) return null;
    return `${result.stdout || ""}${result.stderr || ""}`.trim().split("\\n")[0] || null;
  },
  async install() { return managed(); },
  async uninstall() { return managed(); },
} };
"""

    name = "src/stores/settingsStore.ts"
    sources[name] = replace(
        sources[name],
        'telemetryEnabled: readBoolean("telemetryEnabled", false)',
        "telemetryEnabled: false",
    )
    sources[name] = replace(
        sources[name],
        'setTelemetryEnabled: createBooleanSetter("telemetryEnabled")',
        "setTelemetryEnabled: () => set({ telemetryEnabled: false })",
    )
    sources[name] = replace(
        sources[name],
        'insightsSyncEnabled: readBoolean("insightsSyncEnabled", false)',
        "insightsSyncEnabled: false",
    )
    sources[name] = replace(
        sources[name],
        'setInsightsSyncEnabled: createBooleanSetter("insightsSyncEnabled")',
        "setInsightsSyncEnabled: () => set({ insightsSyncEnabled: false })",
    )
    sources[name] = replace(
        sources[name],
        'autoUpdatesEnabled: readBoolean("autoUpdatesEnabled", true)',
        "autoUpdatesEnabled: false",
    )
    sources[name] = region(
        sources[name],
        "  setAutoUpdatesEnabled: (enabled: boolean) => {",
        "  setGcalPrimaryOnly: (value: boolean) => {",
        "  setAutoUpdatesEnabled: () => set({ autoUpdatesEnabled: false }),\n",
    )

    for name, text in sources.items():
        (root / name).write_text(text)


if __name__ == "__main__":
    prepare(Path(sys.argv[1]).resolve())
