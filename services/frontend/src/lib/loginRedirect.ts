/**
 * The one way to send someone to sign in and bring them back.
 *
 * Three places used to build this themselves and disagreed: the axios 401
 * handler (?next=), the RequireAuth guard (router state, which LoginPage never
 * read) and the OAuth consent page. A signed-out deep link, a forced password
 * change or an expired session on an AI assistant's "Allow?" page each lost
 * the user's place — the last one broke the first Claude connection
 * (2026-10-23). Everything goes through here now.
 */

type Loc = { pathname: string; search: string; hash: string };

// Pages that are themselves the destination of a sign-in redirect, or need no
// sign-in: never a return target (and /login must not loop to itself).
const NO_RETURN = new Set(["/", "/login", "/signup"]);

/** "/login?next=<path?query#hash>", or plain "/login" from a page with nothing to return to. */
export function loginUrl(loc: Loc): string {
  if (NO_RETURN.has(loc.pathname)) return "/login";
  return "/login?next=" + encodeURIComponent(loc.pathname + loc.search + loc.hash);
}

/**
 * A `next` value made safe to navigate to: an absolute path on THIS origin, or
 * `fallback`. Parsed with URL against our own origin rather than string-checked:
 * "//evil.com", "/\\evil.com" (browsers read "\\" as "/") and "https://evil.com"
 * all resolve to another origin and are refused.
 */
export function safeNext(raw: string | null | undefined, fallback = "/jobs"): string {
  if (!raw || !raw.startsWith("/")) return fallback;
  let url: URL;
  try {
    url = new URL(raw, window.location.origin);
  } catch {
    return fallback;
  }
  if (url.origin !== window.location.origin) return fallback;
  if (NO_RETURN.has(url.pathname)) return fallback;
  return url.pathname + url.search + url.hash;
}
