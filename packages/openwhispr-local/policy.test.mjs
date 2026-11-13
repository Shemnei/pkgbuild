import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import test from "node:test";

const require = createRequire(import.meta.url);
const local = path.dirname(fileURLToPath(import.meta.url));
const root = process.env.OPENWHISPR_SOURCE;

function load(relative, fallback) {
  const filename = root
    ? path.join(root, relative)
    : path.join(local, fallback);
  const module = { exports: {} };
  vm.runInNewContext(readFileSync(filename, "utf8"), {
    module,
    require: (name) => {
      if (name === "electron") return { app: { getVersion: () => "1.11.0" } };
      throw new Error(`Forbidden updater import: ${name}`);
    },
    setInterval: () => assert.fail("Periodic updater scheduled"),
    setTimeout: () => assert.fail("Startup updater scheduled"),
  });
  return module.exports;
}

test("pacman owns all update entry points, even after settings enable them", async () => {
  const UpdateManager = load("src/updater.js", "updater.cjs");
  const manager = new UpdateManager();
  manager.setWindowManager({});
  manager.setAutoUpdatesEnabled(true);
  manager.checkForUpdatesOnStartup();
  for (const method of ["checkForUpdates", "downloadUpdate", "installUpdate"]) {
    const result = await manager[method]();
    assert.match(result.message, /pacman/);
    assert.ok(!result.success && !result.updateAvailable);
  }
  assert.equal((await manager.getUpdateStatus()).isSupported, false);
  assert.equal((await manager.getAppVersion()).version, "1.11.0");
  assert.equal(await manager.getUpdateInfo(), null);
  assert.equal(manager.hasStagedUpdate(), false);
  manager.deferInstallOnQuit();
  manager.cleanup();
});

test("GPU software cannot be installed, migrated, or removed by the app", async () => {
  const Manager = load("src/helpers/gpuBinaryManager.js", "gpu-binary.cjs");
  const manager = new Manager({ name: "test" });
  await assert.rejects(manager.download(), /pacman/);
  assert.equal(manager.isSupported(), false);
  assert.equal(manager.getBinaryPath(), null);
  assert.equal(manager.cancelDownload(), false);
  assert.equal((await manager.delete()).deletedCount, 0);
  assert.equal(Manager.migrateLegacyBinDir([manager]).length, 0);
  assert.equal(Manager.detectOutdatedGpuPacks([manager]).length, 0);
});

// Keep this test runnable without fetching upstream or installing dependencies.
test("launcher retains system Electron's sandbox", () => {
  const launcher = readFileSync(path.join(local, "openwhispr"), "utf8");
  assert.match(
    launcher,
    /exec \/usr\/bin\/electron43 --class=open-whispr \/usr\/lib\/openwhispr "\$@"/,
  );
  assert.doesNotMatch(launcher, /no-sandbox|disable.*sandbox/);
  assert.ok(
    require("node:fs").existsSync(path.join(local, "open-whispr.desktop")),
  );
});
