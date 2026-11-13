import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

const root = process.env.OPENCHAMBER_SOURCE;
assert.ok(root, 'Set OPENCHAMBER_SOURCE to the prepared upstream tree');
const load = (name) => import(pathToFileURL(path.join(root, name)).href);
const source = (name) => readFile(path.join(root, name), 'utf8');

test('update API cannot report usage, create identifiers, or fetch a release', async () => {
  const original = globalThis.fetch;
  const originalApi = process.env.OPENCHAMBER_UPDATE_API_URL;
  globalThis.fetch = () => { throw new Error('Unexpected outbound request'); };
  process.env.OPENCHAMBER_UPDATE_API_URL = 'https://must-not-be-used.invalid';
  try {
    const manager = await load('packages/web/server/lib/package-manager.js');
    for (const appType of ['web', 'desktop-electron', 'mobile-capacitor', 'vscode']) {
      const result = await manager.checkForUpdates({ appType, reportUsage: true, installId: 'tracking-id' });
      assert.equal(result.available, false);
      assert.equal(result.currentVersion, '2.7.0');
      assert.equal(result.packageManager, 'pacman');
    }
    assert.throws(() => manager.getUpdateCommand('npm'), /pacman/);
    assert.equal(manager.executeUpdate('npm').success, false);
    assert.equal(manager.detectPackageManager(), 'pacman');
    const text = await source('packages/web/server/lib/package-manager.js');
    assert.doesNotMatch(text, /fetch\(|installId|randomUUID|writeFile|spawn/);
  } finally {
    globalThis.fetch = original;
    if (originalApi === undefined) delete process.env.OPENCHAMBER_UPDATE_API_URL;
    else process.env.OPENCHAMBER_UPDATE_API_URL = originalApi;
  }
});

test('CLI update refuses before discovery, shutdown, imports, or downloads', async () => {
  const { createUpdateCommand } = await load('packages/web/bin/lib/commands-update.js');
  const forbidden = new Proxy({}, { get() { throw new Error('Unexpected updater dependency'); } });
  await assert.rejects(createUpdateCommand(forbidden)(), /pacman/);
});

test('OpenCode installer and upgrade implementations cannot start processes', async () => {
  const capability = await load('packages/web/server/lib/opencode/upgrade-capability.js');
  assert.deepEqual(capability.resolveOpenCodeUpgradeCapability(), {
    supported: false, manager: 'pacman', reason: 'policy',
  });
  const installer = await load('packages/web/server/lib/opencode/v2-install.js');
  assert.equal(installer.supportsOpenCodeV2Install(), false);
  await assert.rejects(installer.installOpenCodeV2(), /pacman/);
  const upgrade = await load('packages/web/server/lib/opencode/cli-upgrade.js');
  await assert.rejects(upgrade.runOpenCodeCliUpgrade(), /pacman/);
});

test('server routes refuse installation and never call desktop updater hooks', async () => {
  const { registerOpenChamberRoutes } = await load('packages/web/server/lib/opencode/openchamber-routes.js');
  const handlers = new Map();
  const app = {
    get: (name, fn) => handlers.set(`GET ${name}`, fn),
    post: (name, fn) => handlers.set(`POST ${name}`, fn),
  };
  const forbidden = () => { throw new Error('Unexpected update operation'); };
  registerOpenChamberRoutes(app, { desktopUpdater: { check: forbidden, install: forbidden, restart: forbidden } });
  const response = {
    statusCode: 200,
    status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; return this; },
  };
  await handlers.get('GET /api/openchamber/update-check')({ query: { reportUsage: 'true' } }, response);
  assert.equal(response.body.available, false);
  await handlers.get('POST /api/openchamber/update-install')({}, response);
  assert.equal(response.statusCode, 409);
  assert.equal(response.body.code, 'PACMAN_MANAGED');
});

test('browser update entry points and polling contain no tracking or network work', async () => {
  const store = await source('packages/ui/src/stores/useUpdateStore.ts');
  assert.doesNotMatch(store, /runtimeFetch|fetch\(|localStorage|randomUUID|checkForDesktopUpdates/);
  const polling = await source('packages/ui/src/hooks/useUpdatePolling.ts');
  assert.doesNotMatch(polling, /setTimeout|setInterval|useEffect|checkForUpdates\(/);
  const guests = await source('packages/ui/src/hooks/useGuestUpdateNotice.ts');
  assert.doesNotMatch(guests, /setTimeout|setInterval|checkGuestUpdates|localStorage/);
  const opencode = await source('packages/ui/src/components/update/OpenCodeUpdateToast.tsx');
  assert.doesNotMatch(opencode, /setTimeout|setInterval|fetchOpenCodeUpgradeStatus|runOpenCodeUpgrade/);
  const entry = await source('packages/web/src/main.tsx');
  assert.doesNotMatch(entry, /registerSW|serviceWorker|registerPwaServiceWorker/);
  assert.match(await source('packages/web/vite.config.ts'), /VitePWA\(\{\s+disable: true/);
  assert.match(await source('packages/ui/src/stores/useUIStore.ts'), /setReportUsage: \(\) => \{\s+set\(\{ reportUsage: false/);
});

test('OpenCode version checks and install endpoints have no remote upgrade work', async () => {
  const routes = await source('packages/web/server/lib/opencode/routes.js');
  assert.doesNotMatch(routes, /fetchLatestOpenCodeVersion|resolveNpmRegistryRequest|upgradeOpenCodeCli|installOpenCodeV2/);
  assert.match(routes, /app\.post\('\/api\/opencode\/install-v2', refuseUpdate\)/);
  assert.match(routes, /app\.post\('\/api\/opencode\/upgrade', refuseUpdate\)/);
  assert.match(await source('packages/web/server/lib/opencode/compatibility.js'), /canInstall: false/);
});

test('OpenCode route handlers reject updates without touching the managed process', async () => {
  const { registerOpenCodeRoutes } = await load('packages/web/server/lib/opencode/routes.js');
  const { resolveOpenCodeUpgradeCapability } = await load('packages/web/server/lib/opencode/upgrade-capability.js');
  const handlers = new Map();
  const app = Object.fromEntries(['get', 'post', 'put', 'delete', 'use'].map((method) => [
    method, (name, ...callbacks) => handlers.set(`${method.toUpperCase()} ${name}`, callbacks.at(-1)),
  ]));
  const forbidden = () => { throw new Error('Unexpected process, network, or installer operation'); };
  registerOpenCodeRoutes(app, {
    getOpenCodeUpgradeCapability: resolveOpenCodeUpgradeCapability,
    upgradeOpenCodeCli: forbidden,
    installOpenCodeV2: forbidden,
    buildOpenCodeUrl: forbidden,
  });
  for (const name of ['POST /api/opencode/install-v2', 'POST /api/opencode/upgrade']) {
    const response = {
      status(code) { this.statusCode = code; return this; },
      json(body) { this.body = body; return this; },
    };
    await handlers.get(name)({}, response);
    assert.equal(response.statusCode, 409);
    assert.equal(response.body.code, 'PACMAN_MANAGED');
  }
  const response = { json(body) { this.body = body; } };
  await handlers.get('GET /api/opencode/upgrade-status')({}, response);
  assert.equal(response.body.available, false);
  assert.equal(response.body.upgrade.supported, false);
});
