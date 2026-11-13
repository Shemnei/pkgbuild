import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { access, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { after, test } from 'node:test';
import { pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const root = process.env.OPENCHAMBER_STAGED;
assert.ok(root, 'Set OPENCHAMBER_STAGED to the staged production package');
const temporary = await mkdtemp(path.join(tmpdir(), 'openchamber-smoke-'));
after(() => rm(temporary, { recursive: true, force: true }));
const run = promisify(execFile);
const options = {
  timeout: 30_000,
  env: {
    ...process.env,
    HOME: temporary,
    XDG_CONFIG_HOME: path.join(temporary, 'config'),
    OPENCHAMBER_DATA_DIR: path.join(temporary, 'data'),
    DO_NOT_TRACK: '1',
    OPENCODE_DISABLE_AUTOUPDATE: '1',
  },
};

test('staged CLI reports its version and loads its help without development dependencies', async () => {
  const cli = path.join(root, 'bin/cli.js');
  const version = await run(process.execPath, [cli, '--version'], options);
  assert.match(version.stdout, /2\.7\.0/);
  const help = await run(process.execPath, [cli, '--help'], options);
  assert.match(help.stdout, /usage/i);
});

test('staged CLI refuses self-update without touching an OpenCode installation', async () => {
  await assert.rejects(
    run(process.execPath, [path.join(root, 'bin/cli.js'), 'update'], options),
    (error) => error.code === 1 && /pacman/.test(error.stderr + error.stdout),
  );
});

test('staged server imports with only the production dependency closure', async () => {
  const url = pathToFileURL(path.join(root, 'server/index.js')).href;
  await run(process.execPath, ['--input-type=module', '-e',
    'const server = await import(process.argv[1]); process.exit(typeof server.startWebUiServer === "function" ? 0 : 1);', url,
  ], options);
});

test('staged terminal addon spawns and reaps a real process', async () => {
  await run(process.execPath, ['-e',
    'const pty = require(process.argv[1]); const p = pty.spawn("/usr/bin/true", [], {name: "xterm", cols: 80, rows: 24}); p.onExit(({exitCode}) => {process.exitCode = exitCode;});',
    path.join(root, 'node_modules/node-pty'),
  ], options);
});

test('built web assets exist and no service worker or Electron runtime is shipped', async () => {
  await access(path.join(root, 'dist/index.html'));
  await assert.rejects(access(path.join(root, 'dist/sw.js')), { code: 'ENOENT' });
  await assert.rejects(access(path.join(root, 'node_modules/electron')), { code: 'ENOENT' });
});
