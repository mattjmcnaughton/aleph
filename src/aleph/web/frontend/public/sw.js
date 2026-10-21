// Notification-only worker. Tested directly in sw.test.ts: no duplicated build copy.
function safeSameOrigin(value) {
  if (typeof value !== "string" || !value || value.length > 4096) return null;
  try {
    const url = new URL(value, self.location.origin);
    if (url.origin !== self.location.origin || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

function parse(data) {
  try {
    const msg = data.json();
    const n = msg.notification;
    if (msg.web_push !== 8030 || !n || typeof n !== "object") return null;
    if (typeof n.title !== "string" || !n.title || n.title.length > 256) return null;
    if (typeof n.body !== "string" || n.body.length > 2048) return null;
    if (n.tag !== undefined && (typeof n.tag !== "string" || n.tag.length > 256)) return null;
    if (n.lang !== undefined && (typeof n.lang !== "string" || n.lang.length > 64)) return null;
    const navigate = safeSameOrigin(n.navigate);
    return navigate ? { ...n, navigate } : null;
  } catch {
    return null;
  }
}

self.addEventListener("push", (event) => {
  const n = parse(event.data);
  if (!n) return;
  event.waitUntil(
    self.registration.showNotification(n.title, {
      body: n.body,
      tag: n.tag,
      lang: n.lang,
      data: { navigate: n.navigate },
    }),
  );
});

async function openOrFocus(url) {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const client =
    clients.find((c) => safeSameOrigin(c.url) === url) ??
    clients.find((c) => safeSameOrigin(c.url));
  if (client) {
    try {
      const navigated = await client.navigate(url);
      if (navigated) {
        await navigated.focus();
        return;
      }
    } catch {
      // A window can disappear after matchAll; recover the notification tap.
    }
  }
  await self.clients.openWindow(url);
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = safeSameOrigin(event.notification.data?.navigate) ?? `${self.location.origin}/`;
  event.waitUntil(openOrFocus(url));
});
