// Downstream policy: pacman owns software updates. No registry or child processes.
import { fileURLToPath } from 'node:url';

const PACMAN_MESSAGE = 'OpenSpec software updates are managed by pacman (pacman -Syu).';

export interface CliInstallInfo {
  location: string | null;
  packageManager: 'pacman';
  scope: 'global';
}

export interface CliUpdateCheck {
  status: 'available' | 'current' | 'disabled' | 'offline';
  latest: string | null;
}

export function getInstallDir(): string {
  return fileURLToPath(new URL('../../', import.meta.url));
}

export function getCliInstallInfo(): CliInstallInfo {
  return { location: getInstallDir(), packageManager: 'pacman', scope: 'global' };
}

export function isSourceCheckout(_installDir: string | null): boolean {
  return false;
}

export async function checkForCliUpdate(
  _options: { requested?: boolean } = {}
): Promise<CliUpdateCheck> {
  return { status: 'disabled', latest: null };
}

export async function getAvailableCliUpdate(): Promise<string | null> {
  return null;
}

export function getCliUpdateCommand(_install: CliInstallInfo): string {
  return 'pacman -Syu';
}

export function canSelfUpgrade(_installDir: string | null, _projectPath: string): boolean {
  return false;
}

export function shouldOfferUpgrade(_params: {
  installDir: string | null;
  projectPath: string;
  interactive: boolean;
  stdoutIsTty: boolean;
}): boolean {
  return false;
}

export type UpgradeOutcome = 'upgraded' | 'declined' | 'failed' | 'cancelled' | 'not-on-path';

export async function offerCliUpgrade(_latestVersion: string): Promise<UpgradeOutcome> {
  console.error(PACMAN_MESSAGE);
  return 'declined';
}

export async function rerunUpdateWithUpgradedCli(
  _projectPath: string,
  _options: { force?: boolean; binPath?: string } = {}
): Promise<number> {
  console.error(PACMAN_MESSAGE);
  return 1;
}

export function displayCliUpdateNote(
  _latestVersion: string,
  _projectPath = '.',
  _options: { withCommand?: boolean } = {}
): void {
  console.error(PACMAN_MESSAGE);
}

export function displayUpgradeCommand(_projectPath = '.'): void {
  console.error(PACMAN_MESSAGE);
}

export function buildVersionReportLines(
  version: string,
  _install: CliInstallInfo,
  _update?: CliUpdateCheck,
  _command: string | null = null
): string[] {
  return [`OpenSpec ${version} (pacman, global)`, PACMAN_MESSAGE];
}
