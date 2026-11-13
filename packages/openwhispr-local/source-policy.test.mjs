import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import vm from "node:vm";
import test from "node:test";
import { pathToFileURL } from "node:url";

assert.ok(process.env.OPENWHISPR_SOURCE, "OPENWHISPR_SOURCE is required");
const root = path.resolve(process.env.OPENWHISPR_SOURCE);
const require = createRequire(path.join(root, "package.json"));

function load(relative, mocks = {}) {
  const module = { exports: {} };
  vm.runInNewContext(readFileSync(path.join(root, relative), "utf8"), {
    module,
    require: (name) => {
      if (Object.hasOwn(mocks, name)) return mocks[name];
      throw new Error(`Unexpected dependency at policy boundary: ${name}`);
    },
    URL,
  });
  return module.exports;
}

test("backend rejects analytics uploads but retains erasure and ordinary Cloud calls", async () => {
  const { createCloudApiRequestHandler } = load(
    "src/helpers/cloudApiRequest.js",
    {
      "./policyRequestHeaders": {
        withPolicyRequestHeaders: (headers) => headers,
      },
    },
  );
  const requests = [];
  const handler = createCloudApiRequestHandler({
    getApiUrl: () => "https://example.invalid",
    getAppVersion: () => "1.11.0",
    tokenStore: { getState: () => ({ token: "fixture", generation: 1 }) },
    proxyFetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  for (const route of [
    "/api/analytics/events/batch",
    "/api/analytics/participation",
    "/api/analytics/summary?backfill=true",
    "/api/%61nalytics/events/batch",
    "/api/notes/../analytics/events/batch",
  ]) {
    const result = await handler({ path: route, method: "POST", public: true });
    assert.equal(result.success, false);
    assert.match(result.error, /analytics.*disabled/);
  }
  assert.equal(requests.length, 0);
  for (const [route, method] of [
    ["/api/analytics/events/delete", "DELETE"],
    ["/api/notes", "POST"],
  ]) {
    assert.equal(
      (await handler({ path: route, method, expectedAuthGeneration: 1 }))
        .success,
      true,
    );
  }
  assert.equal(requests.length, 2);
});

test("yt-dlp updater cannot touch the network or spawn a child", async () => {
  const source = readFileSync(
    path.join(root, "src/helpers/urlAudioDownloader.js"),
    "utf8",
  );
  const module = { exports: {} };
  vm.runInNewContext(source, {
    module,
    process,
    URL,
    require: (name) => {
      if (name === "child_process")
        return { spawn: () => assert.fail("Updater spawned") };
      if (name === "./debugLogger") return {};
      if (name === "./safeTempDir") return {};
      if (name === "./ffmpegUtils") return {};
      return require(name);
    },
  });
  assert.match(
    (await module.exports.maybeUpdateYtDlp({ force: true })).message,
    /pacman/,
  );
  assert.doesNotMatch(
    source,
    /--update-to|seedYtDlpFromBundle|YT_DLP_CACHE_DIR/,
  );
  assert.match(
    source,
    /"--ignore-config",\s*"--no-update",\s*"--no-remote-components"/,
  );
  assert.equal(
    module.exports.detectUrlType("https://youtu.be/abcdefghijk"),
    "youtube",
  );
});

test("both Better Auth export conditions ignore reporting environment and callbacks", async () => {
  for (const name of ["index.mjs", "node.mjs"]) {
    const { createTelemetry, getTelemetryAuthConfig } = await import(
      pathToFileURL(
        path.join(root, "node_modules/@better-auth/telemetry/dist", name),
      )
    );
    const reporter = await createTelemetry(
      { telemetry: { enabled: true, debug: true } },
      { customTrack: () => assert.fail("Telemetry callback invoked") },
    );
    await reporter.publish({ type: "test", payload: { secret: true } });
    assert.deepEqual(await getTelemetryAuthConfig(), {});
  }
});

test("local Insights survive, but serialized Cloud diagnostics do not", () => {
  const source = readFileSync(
    path.join(root, "src/helpers/ipcHandlers.js"),
    "utf8",
  );
  for (const [begin, end] of [
    [
      "const multipartFields = {",
      'debugLogger.debug("Cloud transcribe request"',
    ],
    ["requestPurpose: opts.requestPurpose,", "if (!response.ok) {"],
    ["              audioDurationSeconds,", "if (response.status === 401) {"],
  ]) {
    const start = source.indexOf(begin);
    assert.ok(start >= 0);
    const block = source.slice(start, source.indexOf(end, start));
    assert.doesNotMatch(
      block,
      /sessionId:|analyticsOccurredAt:|orukeetLanguageRouting:/,
    );
    assert.doesNotMatch(block, /sendLogs: opts\.sendLogs/);
  }
  assert.match(source, /analytics-record-event/);
  assert.match(source, /analytics-get-summary/);
  assert.doesNotMatch(source, /this\.sessionId/);
});

test("desktop registration does not overwrite user MIME defaults", () => {
  const { registerLinuxUrlSchemeHandler } = load(
    "src/helpers/linuxUrlSchemeHandler.js",
  );
  assert.equal(
    registerLinuxUrlSchemeHandler("openwhispr", []).registered,
    false,
  );
  const main = readFileSync(path.join(root, "main.js"), "utf8");
  assert.match(
    main,
    /function registerOpenWhisprProtocol\(\) \{\s*if \(process.platform === "linux"\) return isOAuthSchemeRegistered\(\);/,
  );
});

test("autostart launches the pacman-owned wrapper without touching user state", () => {
  const { resolveExecutablePath } = load("src/helpers/linuxAutostart.js", {
    fs: {},
    path: require("node:path"),
    os: require("node:os"),
    "./autoStartPolicy": { HIDDEN_LAUNCH_FLAG: "--hidden" },
  });
  assert.equal(resolveExecutablePath(), "/usr/bin/openwhispr");
});

test("startup never starts optional input daemons or prompts for permission changes", async () => {
  const source = readFileSync(
    path.join(root, "src/helpers/ensureYdotool.js"),
    "utf8",
  );
  const { ensureYdotool, getYdotoolStatus } = load(
    "src/helpers/ensureYdotool.js",
    {
      fs: {},
      os: {},
      child_process: {
        spawnSync: () => assert.fail("Startup spawned a process"),
      },
      "./linuxSession": {},
    },
  );
  await ensureYdotool();
  assert.equal(typeof getYdotoolStatus, "function");
  assert.doesNotMatch(source, /showMessageBox|"start"|usermod|sudo/);
});

test("local Whisper resolves only the pacman-owned bundled CPU server", () => {
  const source = readFileSync(
    path.join(root, "src/helpers/whisperServer.js"),
    "utf8",
  );
  const begin = source.indexOf("  getServerBinaryPath() {");
  assert.ok(begin >= 0);
  const end = source.indexOf("  isAvailable() {", begin);
  assert.ok(end > begin);
  const expected = path.join(root, "resources/bin/whisper-server");
  let installed = true;
  const selector = vm.runInNewContext(`({${source.slice(begin, end)}})`, {
    __dirname: path.join(root, "src/helpers"),
    path,
    fs: { existsSync: (candidate) => installed && candidate === expected },
  });
  assert.equal(selector.getServerBinaryPath({ preferCuda: true }), expected);
  assert.equal(selector.getServerBinaryPath({ preferVulkan: true }), expected);
  installed = false;
  assert.equal(selector.getServerBinaryPath(), null);
});

test("manual Llama install/removal leave software management to pacman", async () => {
  const { default: installer } = load("src/helpers/llamaCppInstaller.js", {
    fs: {
      constants: { X_OK: 1 },
      accessSync: () => {
        throw new Error("fixture: not installed");
      },
    },
    child_process: {
      spawnSync: () => assert.fail("Installer spawned a process"),
    },
  });
  assert.equal(await installer.isInstalled(), false);
  assert.equal(await installer.getVersion(), null);
  for (const method of ["install", "uninstall"]) {
    const result = await installer[method]();
    assert.equal(result.success, false);
    assert.match(result.message, /pacman/);
  }
});

test("application resources are remapped without assigning Electron's readonly property", () => {
  const source = readFileSync(path.join(root, "main.js"), "utf8");
  const statement = source.match(
    /Object\.defineProperty\(process, "resourcesPath", \{ value: __dirname \}\);/,
  );
  assert.ok(statement);
  const electronProcess = {};
  Object.defineProperty(electronProcess, "resourcesPath", {
    value: "/usr/lib/electron43/resources",
    configurable: true,
    writable: false,
  });
  vm.runInNewContext(statement[0], {
    process: electronProcess,
    __dirname: root,
  });
  assert.equal(electronProcess.resourcesPath, root);
});
