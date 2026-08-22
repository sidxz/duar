/**
 * Path prefix the SPA is served under, read from `<base href>` in index.html
 * ("/" at root, "/duar-admin/" under a prefix). The container injects it at start
 * from DUAR_ADMIN_BASE_PATH, so one published image serves any prefix.
 */
export const BASE_PATH = new URL(document.baseURI).pathname;
