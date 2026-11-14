// Downstream policy: reporting and tracking identifiers cannot be enabled.
export function isTelemetryEnabled(): boolean {
  return false;
}

export async function getOrCreateAnonymousId(): Promise<string> {
  return '';
}

export async function trackCommand(_commandName: string, _version: string): Promise<void> {}

export async function maybeShowTelemetryNotice(
  _options: { silent?: boolean } = {}
): Promise<void> {}

export async function shutdown(): Promise<void> {}
