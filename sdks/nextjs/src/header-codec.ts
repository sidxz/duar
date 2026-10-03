/**
 * HTTP header values are ByteStrings (Latin-1): Headers.set() throws a
 * TypeError for any code point >255, so forwarding a raw display name like
 * "中文" or "Иван" would crash inside the middleware's try/catch and be
 * misclassified as an auth failure (infinite login redirect). Percent-encode
 * on write, decode on read — always, so the wire format is deterministic.
 */
export function encodeHeaderValue(value: string): string {
  return encodeURIComponent(value)
}

export function decodeHeaderValue(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    // Not produced by encodeHeaderValue (e.g. header set by something else) —
    // return as-is rather than dropping the value.
    return value
  }
}
