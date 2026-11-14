// Do not download or execute upstream installers, even on explicit invocation.
export async function installStandalone(_options: { version?: string } = {}): Promise<void> {
  throw new Error('OpenSpec software is managed by pacman; use pacman -Syu.');
}
