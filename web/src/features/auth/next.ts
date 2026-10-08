/**
 * The `next` parameter of /login: where to go once signed in. Only a path of this site is
 * followed. Anything that could leave it (`//host`, `/\host`, `https:`, `javascript:`, control
 * characters that browsers strip, encoded forms of these) falls back to "/".
 */
export const DEFAULT_NEXT = "/";

export function safeNext(raw: string | null | undefined): string {
  if (!raw) return DEFAULT_NEXT;
  // Browsers drop tabs and newlines inside URLs and read a backslash as a slash, so
  // "/\t/evil.com" and "/\\evil.com" would both become "//evil.com".
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\\]/.test(raw)) return DEFAULT_NEXT;
  if (!raw.startsWith("/") || raw.startsWith("//")) return DEFAULT_NEXT;
  const origin = window.location.origin;
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return DEFAULT_NEXT;
  }
  if (url.origin !== origin) return DEFAULT_NEXT;
  const path = `${url.pathname}${url.search}${url.hash}`;
  // Going back to the sign in page after signing in would only bounce.
  if (url.pathname === "/login" || path.startsWith("//")) return DEFAULT_NEXT;
  return path;
}

/** /login?next=<the page to come back to>, slashes left readable: /login?next=/admin/users. */
export function loginPath(location: {
  pathname: string;
  search?: string;
  hash?: string;
}): string {
  if (location.pathname === "/login") return "/login";
  const back = `${location.pathname}${location.search ?? ""}${location.hash ?? ""}`;
  return `/login?next=${encodeURIComponent(back).replace(/%2F/g, "/")}`;
}
