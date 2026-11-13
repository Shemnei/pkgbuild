"""Apply the reviewed 2.7.0 policy rewrite; refuse unreviewed upstream drift.

These are replacements at the existing owning modules, not parallel update
implementations or a launcher-only opt-out. Whole-file hashes make the textual
boundaries explicit and prevent a release bump from silently bypassing policy.
"""

import argparse
import hashlib
from pathlib import Path

EXPECTED = {
    "packages/web/server/lib/package-manager.js": "da2fcede848d24341c3d8dd397831e0e60279ef3c498a5fd88bc6d85ee9d270e",
    "packages/web/server/lib/opencode/openchamber-routes.js": "6a0b283d61c98125b8f1a11dffc6daf8d45495e3b0332f0c38da2b42cc3a3ce9",
    "packages/web/server/lib/opencode/routes.js": "32d9021ad257b1acedaee9781109b8868b8b70977403a5d984232489b6a73411",
    "packages/web/server/lib/opencode/compatibility.js": "823db1dda4043bac2a7dd6b1cfcd44e84539d054ecc5a1dea7a7ab3c3a830095",
    "packages/web/server/lib/opencode/upgrade-capability.js": "4c1a35d9bf693fe6a5b385fc9444bdef3cd74a55e98938844df018830b34ea31",
    "packages/web/server/lib/opencode/cli-upgrade.js": "178cddaa9a1482ee1b0aab7c67ab60df2c375adacb99c5e0ac6f94fec5c14963",
    "packages/web/server/lib/opencode/v2-install.js": "c105f30cd51843552256c9825f7d61ba8e539676882fc0b256ca0d7d224650db",
    "packages/web/server/lib/opencode/settings-helpers.js": "209495b7ef8ef490dcaf84b618355734c7bde3a202ab53099f2d9a35598b292b",
    "packages/web/bin/lib/commands-update.js": "456c19c28c9318acc52d423bf9da5e0f6a9599b14ca53b11726b0638b8692a1e",
    "packages/ui/src/stores/useUpdateStore.ts": "c5cdb66c13d218139df9193750e9fd978e8bf5780097cc17dbaccf259458f932",
    "packages/ui/src/hooks/useUpdatePolling.ts": "686801d0b20d6e068e278fc5241ec3dbdd03ad4cca5189f1eb2cea546a2cf783",
    "packages/ui/src/hooks/useGuestUpdateNotice.ts": "118ea98f419e236a6113edafd6da2bf6626d402c53f76e2fcb902b956a4683bb",
    "packages/ui/src/components/update/OpenCodeUpdateToast.tsx": "3306c70243a019b8c810af2a4200844065b5f4a808116db12ad1d2b006a46374",
    "packages/ui/src/stores/useUIStore.ts": "75f89c95e043761c02a106b6a1b6eff48881ab22c59c46189f853807272c630c",
    "packages/web/src/main.tsx": "0134a1f72957dfa42606815b76c43e286fd8945c2916df41b8ea7a7dc3c94c40",
    "packages/web/vite.config.ts": "32b7be45f325f0a1f5eabec96ca9999b1d10273f678b850bdee1f32da277ceff",
}

PACKAGE_MANAGER = """// Arch Linux: pacman owns this installation. No reporting or network checks.
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const message = 'Self-updates are disabled. Update openchamber-web-local using pacman.';

export function getCurrentVersion() {
  return JSON.parse(fs.readFileSync(new URL('../../package.json', import.meta.url), 'utf8')).version;
}

export function detectPackageManagerDetails() {
  return {
    packageManager: 'pacman',
    reason: 'system-package',
    packagePath: fileURLToPath(new URL('../..', import.meta.url)),
    packageManagerCommand: null,
    globalNodeModulesRoot: null,
  };
}

export function detectPackageManager() { return 'pacman'; }
export function isUnmanagedInstall() { return true; }
export function readUpdateChannel() { return 'stable'; }
export function getUpdateCommand() { throw new Error(message); }

export async function checkForUpdates() {
  return {
    available: false,
    currentVersion: getCurrentVersion(),
    packageManager: 'pacman',
    installBlocked: 'manual-install',
  };
}

export function executeUpdate() {
  return { success: false, exitCode: 1, error: message };
}
"""

OPENCHAMBER_ROUTES = """import { publicEnterprisePolicy } from '../enterprise-mode.js';
import { checkForUpdates } from '../package-manager.js';

export const registerOpenChamberRoutes = (app, dependencies) => {
  const { modelsDevApiUrl, modelsMetadataCacheTtl, fetchFreeZenModels, getCachedZenModels } = dependencies;

  app.get('/api/openchamber/enterprise-policy', (_req, res) => {
    res.json(publicEnterprisePolicy());
  });
  app.get('/api/openchamber/update-check', async (_req, res) => {
    res.json(await checkForUpdates());
  });
  app.post('/api/openchamber/update-install', (_req, res) => {
    res.status(409).json({
      success: false,
      code: 'PACMAN_MANAGED',
      error: 'Self-updates are disabled. Update openchamber-web-local using pacman.',
    });
  });

"""

OPENCODE_ROUTES = """  // No installer, registry lookup, or child-process upgrade in an Arch package.
  const refuseUpdate = (_req, res) => res.status(409).json({
    success: false,
    code: 'PACMAN_MANAGED',
    error: 'OpenCode is managed separately. Update it using pacman.',
  });
  app.post('/api/opencode/install-v2', refuseUpdate);
  app.post('/api/opencode/upgrade', refuseUpdate);
  app.get('/api/opencode/upgrade-status', (_req, res) => res.json({
    available: false,
    currentVersion: null,
    latestVersion: null,
    upgrade: getOpenCodeUpgradeCapability(),
  }));

"""

UPDATE_STORE = """export const useUpdateStore = create<UpdateStore>()((set) => ({
  ...initialState,
  checkForUpdates: async () => {
    set({ ...initialState, lastChecked: Date.now() });
    return null;
  },
  downloadUpdate: async () => {
    set({ error: 'Updates are managed by pacman.' });
  },
  restartToUpdate: async () => {
    set({ error: 'Updates are managed by pacman.' });
  },
  dismiss: () => set(initialState),
  reset: () => set(initialState),
}));
"""


def between(text, start, end):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"Expected unique policy boundaries: {start!r}, {end!r}")
    left = text.index(start)
    right = text.index(end, left)
    return text[left:right]


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected exactly one policy target: {old!r}")
    return text.replace(old, new, 1)


def prepare(root):
    texts = {}
    # Validate every input before writing anything.
    for name, expected in EXPECTED.items():
        content = (root / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Unreviewed or already modified upstream file: {name}")
        texts[name] = content.decode()

    texts["packages/web/server/lib/package-manager.js"] = PACKAGE_MANAGER
    name = "packages/web/server/lib/opencode/openchamber-routes.js"
    texts[name] = (
        OPENCHAMBER_ROUTES
        + texts[name][texts[name].index("  app.get('/api/openchamber/models-metadata'") :]
    )

    name = "packages/web/server/lib/opencode/routes.js"
    text = texts[name]
    text = replace_once(
        text, "import { resolveNpmRegistryRequest } from './npm-registry-config.js';\n", ""
    )
    text = replace_once(text, "    upgradeOpenCodeCli,\n", "")
    text = replace_once(text, "    installOpenCodeV2,\n", "")
    text = replace_once(
        text,
        between(text, "  const parseVersionForComparison", "  app.get('/api/config/settings'"),
        "",
    )
    text = replace_once(
        text,
        between(text, "  let installInFlight = null;", "  // OpenCode 2.0.8 removed"),
        OPENCODE_ROUTES,
    )
    texts[name] = text

    name = "packages/web/server/lib/opencode/compatibility.js"
    texts[name] = replace_once(
        texts[name],
        "canInstall: version !== null && isOlderThanMinimum(version) && installation === 'managed' && canInstall,",
        "canInstall: false, // Installation is exclusively managed by pacman.",
    )
    texts[
        "packages/web/server/lib/opencode/upgrade-capability.js"
    ] = """export const resolveOpenCodeUpgradeCapability = () => ({
  supported: false,
  manager: 'pacman',
  reason: 'policy',
});
"""
    texts[
        "packages/web/server/lib/opencode/cli-upgrade.js"
    ] = """export const runOpenCodeCliUpgrade = async () => {
  throw new Error('OpenCode upgrades are disabled. Update OpenCode using pacman.');
};
"""
    texts[
        "packages/web/server/lib/opencode/v2-install.js"
    ] = """export const supportsOpenCodeV2Install = () => false;
export const installOpenCodeV2 = async () => {
  throw new Error('OpenCode installation is disabled. Install OpenCode using pacman.');
};
"""
    texts[
        "packages/web/bin/lib/commands-update.js"
    ] = """export const createUpdateCommand = () => async () => {
  throw new Error('Self-updates are disabled. Update openchamber-web-local using pacman.');
};
"""

    name = "packages/ui/src/stores/useUpdateStore.ts"
    text = texts[name]
    texts[name] = (
        "import { create } from 'zustand';\n"
        "import type { UpdateInfo, UpdateProgress } from '@/lib/desktop';\n\n"
        + between(text, "type UpdateState =", "type ClientRuntime =")
        + between(
            text, "export function parseUpdateCheckResponse", "async function checkForWebUpdates"
        )
        + "export async function checkConnectedServerForUpdates(): Promise<UpdateInfo> {\n"
        "  return { available: false, currentVersion: '2.7.0', packageManager: 'pacman' };\n"
        "}\n\n"
        + between(text, "const initialState:", "export const useUpdateStore =")
        + UPDATE_STORE
    )
    texts[
        "packages/ui/src/hooks/useUpdatePolling.ts"
    ] = """// Arch Linux: no background update checks or usage-reporting check-ins.
export function useUpdatePolling() { return; }
"""
    texts["packages/ui/src/hooks/useGuestUpdateNotice.ts"] = (
        "// User-managed extensions are checked only on explicit request.\n"
        "export const useGuestUpdateNotice = (): void => { return; };\n"
    )
    texts["packages/ui/src/components/update/OpenCodeUpdateToast.tsx"] = (
        "// No automatic OpenCode upgrade checks or update prompts.\n"
        "export const OpenCodeUpdateToast = () => null;\n"
    )
    name = "packages/ui/src/stores/useUIStore.ts"
    texts[name] = replace_once(texts[name], "reportUsage: true,", "reportUsage: false,")
    texts[name] = replace_once(
        texts[name],
        "setReportUsage: (value) => {\n          set({ reportUsage: value });",
        "setReportUsage: () => {\n          set({ reportUsage: false });",
    )
    name = "packages/web/server/lib/opencode/settings-helpers.js"
    texts[name] = replace_once(
        texts[name], "result.reportUsage = candidate.reportUsage;", "result.reportUsage = false;"
    )

    # Do not register a worker that can replace the UI in the background. Existing
    # upstream browser profiles need explicit migration; never erase user caches.
    name = "packages/web/src/main.tsx"
    text = replace_once(texts[name], "import { registerSW } from 'virtual:pwa-register';\n", "")
    text = replace_once(text, between(text, "type PrerenderingDocument", "const start = async"), "")
    texts[name] = text[: text.index("if (import.meta.env.PROD)")]
    name = "packages/web/vite.config.ts"
    texts[name] = replace_once(
        texts[name], "    VitePWA({\n", "    VitePWA({\n      disable: true,\n"
    )

    for name, content in texts.items():
        (root / name).write_text(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    prepare(parser.parse_args().root)
