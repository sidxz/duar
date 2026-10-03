/** Log a warning if the URL uses plain HTTP on a non-localhost host. */
const SAFE_HOSTS = new Set(['localhost', '127.0.0.1', '::1'])

export function warnIfInsecure(url: string, context?: string): void {
  try {
    const parsed = new URL(url)
    if (parsed.protocol === 'http:' && !SAFE_HOSTS.has(parsed.hostname)) {
      const label = context ? ` (${context})` : ''
      console.warn(
        `[duar]${label} Connecting over plain HTTP to ${parsed.hostname}. ` +
          'Use HTTPS in production to protect tokens and credentials.',
      )
    }
  } catch {
    // invalid URL — let the caller handle it
  }
}
