type Level = "debug" | "info" | "warning" | "error";
type Fields = Record<string, unknown>;

interface ClientEvent {
  event: string;
  level: Level;
  fields: Fields;
}

const BASE = "/api";
const FLUSH_MS = 5000;
const MAX_BUFFER = 50;

let buffer: ClientEvent[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
let lastRequestId: string | null = null;

export function setLastRequestId(id: string | null): void {
  if (id) lastRequestId = id;
}

export function getLastRequestId(): string | null {
  return lastRequestId;
}

export function clientLog(event: string, level: Level, fields: Fields = {}): void {
  if (!event.startsWith("client.")) event = `client.${event}`;
  if (import.meta.env.DEV) {
    console[level === "warning" ? "warn" : level]?.(event, fields);
  }
  buffer.push({ event, level, fields: { request_id: lastRequestId, ...fields } });
  if (buffer.length >= MAX_BUFFER) {
    void flush();
  } else if (!timer) {
    timer = setTimeout(() => void flush(), FLUSH_MS);
  }
}

async function flush(): Promise<void> {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  if (buffer.length === 0) return;
  const events = buffer.slice(0, MAX_BUFFER);
  buffer = buffer.slice(MAX_BUFFER);
  try {
    await fetch(`${BASE}/internal/client-logs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ events }),
      keepalive: true,
    });
  } catch {
    // best-effort; drop on failure to avoid unbounded growth
  } finally {
    // A burst >MAX_BUFFER leaves a tail behind; re-arm the timer so it is
    // delivered on the normal cadence instead of waiting for the next
    // clientLog() call (or only on pagehide).
    if (buffer.length > 0 && !timer) {
      timer = setTimeout(() => void flush(), FLUSH_MS);
    }
  }
}

// Flush on page hide.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => void flush());
}
