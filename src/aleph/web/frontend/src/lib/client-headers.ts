// Shared by JSON requests and both streamed conversations. These are hints,
// never credentials; request-local tz_offset_minutes keeps its own semantics.
export function clientHeaders(initial?: HeadersInit): Headers {
  const headers = new Headers(initial);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zone) headers.set("X-Aleph-Time-Zone", zone);
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
  headers.set("X-Aleph-Display-Mode", standalone ? "standalone" : "browser");
  return headers;
}
