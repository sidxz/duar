import { DuarAuth } from "@duar-auth/js";

const DUAR_URL =
  import.meta.env.VITE_DUAR_URL || "http://localhost:9003";
const CLIENT_ID = import.meta.env.VITE_DUAR_CLIENT_ID;
if (!CLIENT_ID) {
  throw new Error(
    "VITE_DUAR_CLIENT_ID is required — get the ClientApp id from the Duar admin panel and set it in .env",
  );
}

/** Shared DuarAuth client instance used by both the React provider and apiFetch. */
export const duarClient = new DuarAuth({
  duarUrl: DUAR_URL,
  clientId: CLIENT_ID,
});

/**
 * Fetch wrapper for the demo backend API.
 * Uses DuarAuth's fetchJson for automatic Bearer token injection, 401 retry, and JSON parsing.
 */
export async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  return duarClient.fetchJson<T>(`/api${path}`, options);
}
