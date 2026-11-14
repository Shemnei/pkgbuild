import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { pathToFileURL } from 'node:url';

const staged = path.resolve(process.env.OPENSPEC_STAGED);
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'openspec-policy-'));
const env = {
  PATH: '/usr/bin:/bin', HOME: home, XDG_CONFIG_HOME: path.join(home, '.config'),
  XDG_DATA_HOME: path.join(home, '.local/share'), NO_COLOR: '1',
  OPENSPEC_TELEMETRY: '1', DO_NOT_TRACK: '0', CI: 'false',
  OPENSPEC_INSTALL_DIR: path.join(home, 'software'),
};

// Count even swallowed errors: the exit hook fails if any forbidden API is tried.
const guard = `
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import child from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
let attempts = 0;
const deny = () => { attempts++; throw new Error('Forbidden network or child process'); };
globalThis.fetch = deny;
for (const api of [http, https]) { api.get = deny; api.request = deny; }
net.connect = deny; net.createConnection = deny; net.Socket.prototype.connect = deny;
tls.connect = deny;
for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) child[name] = deny;
syncBuiltinESMExports();
process.on('exit', () => { if (attempts) process.exitCode = 99; });
`;

function run(script, args = [], expected = 0) {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', guard + script, '--', ...args], {
    cwd: home, env, encoding: 'utf8', timeout: 30000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, expected, result.stdout + result.stderr);
  return result;
}

function moduleUrl(relative) {
  return JSON.stringify(pathToFileURL(path.join(staged, relative)).href);
}

function cli(args, expected = 0) {
  return run(`process.argv = [process.execPath, ${JSON.stringify(path.join(staged, 'bin/openspec.js'))}, ...process.argv.slice(1)];
    await import(${moduleUrl('bin/openspec.js')});`, args, expected);
}

test('reporting cannot be enabled and never creates a tracking identity', () => {
  fs.mkdirSync(env.XDG_CONFIG_HOME, { recursive: true });
  fs.mkdirSync(path.join(env.XDG_CONFIG_HOME, 'openspec'), { recursive: true });
  const config = path.join(env.XDG_CONFIG_HOME, 'openspec/config.json');
  const content = JSON.stringify({ telemetry: { enabled: true, noticeSeen: true } });
  fs.writeFileSync(config, content);
  run(`const m = await import(${moduleUrl('dist/telemetry/index.js')});
    if (m.isTelemetryEnabled() !== false) throw Error('Reporting enabled');
    if (await m.getOrCreateAnonymousId() !== '') throw Error('Tracking ID created');
    await m.trackCommand('init', '2.0.0');
    await m.maybeShowTelemetryNotice(); await m.shutdown();`);
  assert.equal(fs.readFileSync(config, 'utf8'), content);
});

test('all update boundaries remain disabled even on explicit requests', () => {
  run(`const m = await import(${moduleUrl('dist/core/version-check.js')});
    const check = await m.checkForCliUpdate({ requested: true });
    if (check.status !== 'disabled' || check.latest !== null) throw Error('Update check enabled');
    if (await m.getAvailableCliUpdate() !== null) throw Error('Update advertised');
    if (m.canSelfUpgrade('/usr/lib/openspec', '.') || m.shouldOfferUpgrade({interactive: true, stdoutIsTty: true})) throw Error('Upgrade offered');
    if (await m.offerCliUpgrade('999.0.0') !== 'declined') throw Error('Upgrade allowed');
    if (await m.rerunUpdateWithUpgradedCli('.', {binPath: '/forbidden'}) !== 1) throw Error('Child allowed');
    const tip = await import(${moduleUrl('dist/core/standalone-tip.js')});
    await tip.maybeShowStandaloneTip();`);
  const result = cli(['version', '--check', '--json']);
  const report = JSON.parse(result.stdout);
  assert.equal(report.install.packageManager, 'pacman');
  assert.equal(report.update.status, 'disabled');
  assert.equal(report.update.canSelfUpgrade, false);
});

test('manual installer refuses without downloading, spawning, or writing software', () => {
  for (const args of [['install'], ['install', '1.0.0']]) {
    const result = cli(args, 1);
    assert.match(result.stderr, /pacman/);
  }
  assert.equal(fs.existsSync(env.OPENSPEC_INSTALL_DIR), false);
});

test('generated cloud integration requires provisioning, not an upstream installer', () => {
  run(`const m = await import(${moduleUrl('dist/core/github-copilot/cloud-agent.js')});
    const workflow = m.generateCopilotSetupSteps();
    const agent = m.generateCopilotAgentFile();
    if (workflow.includes('npm install') || agent.includes('npm install')) throw Error('Cloud installer advertised');
    if (!workflow.includes('command -v openspec') || !agent.includes('pacman')) throw Error('Provisioning contract missing');`);
});

test('staged dependency closure contains no development tools or escaping links', () => {
  function inspect(directory) {
    const entries = fs.readdirSync(directory, { withFileTypes: true });
    assert.notEqual(entries.length, 0, `Empty runtime directory: ${directory}`);
    for (const entry of entries) {
      const target = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) {
        assert.ok(fs.realpathSync(target).startsWith(staged + path.sep), target);
      } else if (entry.isDirectory()) {
        inspect(target);
      } else if (entry.name === 'package.json') {
        const { name } = JSON.parse(fs.readFileSync(target, 'utf8'));
        assert.ok(!['typescript', 'eslint', 'vitest', 'vite', 'esbuild', '@changesets/cli', '@types/node', 'undici-types'].includes(name), name);
      }
    }
  }
  inspect(staged);
  for (const file of ['dist/telemetry/index.js', 'dist/core/version-check.js', 'dist/commands/install-standalone.js']) {
    assert.doesNotMatch(fs.readFileSync(path.join(staged, file), 'utf8'), /edge\.openspec\.dev|registry\.npmjs\.org|curl|posthog|randomUUID/);
  }
});

process.on('exit', () => fs.rmSync(home, { recursive: true, force: true }));
