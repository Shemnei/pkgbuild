import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { setTimeout } from "node:timers/promises";

const port = process.argv[2];
let page;
for (let attempt = 0; attempt < 120; attempt++) {
  try {
    const pages = await (
      await fetch(`http://127.0.0.1:${port}/json/list`)
    ).json();
    page = pages.find(
      (entry) =>
        entry.type === "page" &&
        entry.url.includes("/src/dist/index.html") &&
        new URL(entry.url).searchParams.get("panel") === "true",
    );
    if (page) break;
  } catch {
    // The real launcher and renderer are still starting.
  }
  await setTimeout(500);
}
assert.ok(page, "Installed launcher did not create a local renderer");
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});
let id = 0;
async function evaluate(expression) {
  const requestId = ++id;
  return new Promise((resolve, reject) => {
    const listener = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== requestId) return;
      socket.removeEventListener("message", listener);
      if (message.error || message.result?.exceptionDetails)
        reject(new Error(event.data));
      else resolve(message.result.result.value);
    };
    socket.addEventListener("message", listener);
    socket.send(
      JSON.stringify({
        id: requestId,
        method: "Runtime.evaluate",
        params: { expression, returnByValue: true, awaitPromise: true },
      }),
    );
  });
}
try {
  let text = "";
  for (let attempt = 0; attempt < 60; attempt++) {
    text = await evaluate("document.body?.innerText ?? ''");
    if (text.trim().length > 30) break;
    await setTimeout(500);
  }
  assert.ok(text.trim().length > 30, "React rendered an empty page");
  assert.doesNotMatch(text, /something went wrong|failed to load application/i);
  const result = JSON.parse(
    await evaluate(
      "window.electronAPI.checkForUpdates().then(result => JSON.stringify(result))",
    ),
  );
  assert.equal(result.updateAvailable, false);
  assert.match(result.message, /pacman/);
  const whisper = JSON.parse(
    await evaluate(
      "window.electronAPI.whisperServerStatus().then(result => JSON.stringify(result))",
    ),
  );
  assert.equal(
    whisper.available,
    true,
    "Bundled CPU engine is unavailable through IPC",
  );
  assert.equal(
    whisper.running,
    false,
    "Fresh app started an engine without a model",
  );
  const autostart = path.join(
    process.env.XDG_CONFIG_HOME,
    "autostart/open-whispr.desktop",
  );
  assert.equal(
    existsSync(autostart),
    false,
    "Startup enabled autostart unasked",
  );
  try {
    assert.equal(
      await evaluate(
        "window.electronAPI.setAutoStartEnabled(true).then(result => result.success)",
      ),
      true,
    );
    assert.match(
      readFileSync(autostart, "utf8"),
      /^Exec="\/usr\/bin\/openwhispr" --hidden$/m,
    );
    execFileSync("desktop-file-validate", [autostart]);
  } finally {
    assert.equal(
      await evaluate(
        "window.electronAPI.setAutoStartEnabled(false).then(result => result.success)",
      ),
      true,
    );
  }
  assert.equal(existsSync(autostart), false);
  console.log(
    "Installed launcher, renderer, preload, updater, CPU-engine IPC, and autostart passed",
  );
} finally {
  socket.close();
}
// Chromium's DevTools endpoint can leave the WebSocket close handshake pending.
// Every assertion above is awaited; the parent owns application teardown.
process.exit(0);
