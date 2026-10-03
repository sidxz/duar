import { DuarAuthz, IdpConfigs } from "@duar-auth/js";

const DUAR_URL =
  process.env.NEXT_PUBLIC_DUAR_URL || "http://localhost:9003";
const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:9200";
const GOOGLE_CLIENT_ID =
  process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

/** Lazy singleton — avoids localStorage access during SSR/prerendering. */
let _client: DuarAuthz | null = null;

export function getAuthzClient(): DuarAuthz {
  if (!_client) {
    _client = new DuarAuthz({
      duarUrl: DUAR_URL,
      // Mint endpoint lives on the demo backend — it holds the Duar service
      // key and proxies the POST /authz/resolve call. Browsers never touch the
      // service key; credential issuance stays server-to-server.
      mintEndpoint: `${BACKEND_URL}/auth/mint`,
      idps: {
        google: IdpConfigs.google(GOOGLE_CLIENT_ID),
      },
    });
  }
  return _client;
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  return getAuthzClient().fetchJson<T>(`${BACKEND_URL}${path}`, options);
}
