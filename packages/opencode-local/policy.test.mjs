// Copied into packages/cli/test by check(), so imports use the pinned workspace.
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { test } from 'bun:test';
import { Effect, Layer } from 'effect';
import { Updater } from '../src/services/updater';
import { Otlp } from '../../util/src/observability/otlp';

const source = (file) => readFile(new URL(file, import.meta.url), 'utf8');

test('updater cannot fetch, install, notify, or be re-enabled by configuration', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('Unexpected update request'); };
  try {
    await Effect.runPromise(Effect.gen(function* () {
      const updater = yield* Updater.Service;
      assert.equal(yield* updater.run(() => { throw new Error('Unexpected install callback'); }), undefined);
      assert.equal((yield* updater.check()).type, 'unavailable');
      assert.match((yield* updater.check()).message, /pacman/);
      assert.equal(yield* updater.method(), undefined);
      for (const method of Updater.methods) {
        assert.equal(updater.removal(method), undefined);
        for (const effect of [updater.latest(method), updater.upgrade(method, '99.0.0'), updater.apply('99.0.0')]) {
          const error = yield* Effect.flip(effect);
          assert.match(error.message, /pacman/);
        }
      }
    }).pipe(Effect.provide(Updater.layer)));
    for (const text of ['{"update":"auto"}', '{"autoupdate":true}', '{"update":"notify"}']) {
      assert.equal(Updater.decodePolicy(text), 'disable');
    }
  } finally {
    globalThis.fetch = original;
  }
  const text = await source('../src/services/updater.ts');
  assert.doesNotMatch(text, /fetch\(|ChildProcess|AppProcess|FileSystem|readFile|https:\/\//);
});

test('manual upgrade and uninstall refuse before destructive or remote work', async () => {
  for (const command of ['upgrade', 'uninstall']) {
    const text = await source(`../src/commands/handlers/${command}.ts`);
    assert.match(text, /Effect\.fail\(new Error\(managedMessage\)\)/);
    assert.doesNotMatch(text, /FileSystem|fetch\(|updater\.(latest|upgrade)|\.remove\(|Service\.stop/);
  }
});

test('explicit OTLP endpoints cannot construct log exporters or tracing SDKs', async () => {
  const app = { client: 'test', channel: 'latest', version: '2.2.7' };
  const options = { endpoint: 'http://127.0.0.1:1', headers: 'authorization=secret' };
  assert.deepEqual(Otlp.loggers(options, app), []);
  assert.equal(await Effect.runPromise(Otlp.tracingLayer(options, app)), Layer.empty);
  const text = await source('../../util/src/observability/otlp.ts');
  assert.doesNotMatch(text, /runID|randomUUID|OtlpLogger|OTLPTraceExporter|import\(/);
  const wiring = await source('../../util/src/observability.ts');
  assert.match(wiring, /Logging\.loggers/);
  assert.doesNotMatch(wiring, /FetchHttpClient|OtlpExporter|Otlp\.loggers|Otlp\.tracingLayer/);
});

test('real log/span execution sends nothing to an explicitly configured loopback collector', async () => {
  const home = await mkdtemp(path.join(os.tmpdir(), 'opencode-policy-'));
  let requests = 0;
  const collector = Bun.serve({
    hostname: '127.0.0.1', port: 0,
    fetch() { requests++; return new Response('{}'); },
  });
  try {
    const endpoint = `http://127.0.0.1:${collector.port}`;
    const child = Bun.spawn([process.execPath, '--eval', `
      import { Effect } from 'effect';
      import { Observability } from '@opencode/util/observability';
      await Effect.logInfo('local-policy-diagnostic').pipe(
        Effect.withSpan('local-policy-span'),
        Effect.provide(Observability.layer({ endpoint: ${JSON.stringify(endpoint)}, channel: 'latest' })),
        Effect.scoped,
        Effect.runPromise,
      );
    `], {
      cwd: path.resolve(import.meta.dir, '../../util'),
      env: {
        PATH: process.env.PATH,
        HOME: home, XDG_CONFIG_HOME: `${home}/config`, XDG_CACHE_HOME: `${home}/cache`,
        XDG_DATA_HOME: `${home}/data`, XDG_STATE_HOME: `${home}/state`,
        OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
        DO_NOT_TRACK: '0', BUN_CONFIG_NO_TELEMETRY: '1',
      },
      stdout: 'pipe', stderr: 'pipe', timeout: 10_000,
    });
    const [code, stdout, stderr] = await Promise.all([
      child.exited, new Response(child.stdout).text(), new Response(child.stderr).text(),
    ]);
    assert.equal(code, 0, stdout + stderr);
    assert.equal(requests, 0);
  } finally {
    collector.stop(true);
    await rm(home, { recursive: true, force: true });
  }
});

test('plugin background checks return before registry work; explicit checks remain', async () => {
  const text = await source('../../core/src/plugin/update.ts');
  assert.match(text, /if \(!options\?\.refresh\) return Effect\.succeed\(false\)\s+return locks\.withLock\(target\)/);
  assert.match(text, /npm\.check\(target\)/);
  assert.match(text, /npm\.update\(target\)/);
});

test('ripgrep and formatters use installed executables, never automatic downloads', async () => {
  const text = await source('../../core/src/ripgrep/binary.ts');
  assert.match(text, /"\/usr\/bin\/rg"/);
  assert.doesNotMatch(text, /HttpClient|fetch\(|download\//);
  const formatters = await source('../../core/src/formatter/builtins.ts');
  assert.doesNotMatch(formatters, /input\.npm\.which/);
  for (const binary of ['prettier', 'oxfmt', 'biome']) {
    assert.ok(formatters.includes(`findExecutable("${binary}")`));
  }
});

test('Claude child process receives privacy flags after inherited environment', async () => {
  const text = await source('../../core/src/plugin/provider/claude-agent/claude/query-options.ts');
  for (const flag of ['DO_NOT_TRACK', 'DISABLE_TELEMETRY', 'DISABLE_ERROR_REPORTING', 'DISABLE_AUTOUPDATER']) {
    assert.ok(text.includes(`${flag}: "1"`));
  }
  assert.match(text, /env: \{ \.\.\.input\.io\.env\(\), \.\.\.input\.connection, \.\.\.ISOLATION_ENV \}/);
});

test('compiler runtime comes from the installed build dependency, not an implicit download', async () => {
  const text = await source('../script/build.ts');
  assert.match(text, /async function compileExecutable[\s\S]*?\{\s*return process\.execPath\s*\}/);
  assert.doesNotMatch(text, /compileReleaseAssets|BUN_COMPILE_RELEASE|fetch\(/);
});
