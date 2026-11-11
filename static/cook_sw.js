// De service worker van MealieShopper, alleen voor de meldingen van de kooktimers (static/cook_timers.js, issue #309): op
// Android en in een geïnstalleerde app kan een pagina zelf geen melding tonen, een service worker wel. Hij vangt geen
// verzoeken af (er is geen fetch-handler) en bewaart niets: de app werkt precies zoals zonder.
self.addEventListener("install", () => self.skipWaiting());

// Web Push van de server (issue #311, mealieshopper/push.py): een timer die afliep terwijl de app gepauzeerd was, op een
// vergrendelde telefoon of lang op de achtergrond. Is de app in beeld, dan piept die zelf: geen melding. Staat de melding er
// al (de app toonde hem net zelf), dan vervangt deze hem zonder opnieuw te trillen: er komt nooit een tweede.
self.addEventListener("push", (event) => {
  let data = null;
  try {
    data = event.data ? event.data.json() : null;
  } catch {
    data = null;
  }
  if (!data || data.type !== "cook-timer" || !data.id) return;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      if (windows.some((client) => client.visibilityState === "visible" && client.focused)) return;
      const tag = `cook-timer-${data.id}`;
      const shown = await self.registration.getNotifications({ tag });
      // Alleen een pad op deze app: een tik op de melding opent nooit een andere site.
      const target = new URL(String(data.url || "/"), self.location.origin);
      await self.registration.showNotification(String(data.title || ""), {
        body: String(data.body || ""),
        tag,
        renotify: shown.length === 0,
        requireInteraction: true,
        vibrate: [300, 150, 300, 150, 300],
        icon: new URL("brand/icon-192.png", self.location.href).href,
        data: { url: target.origin === self.location.origin ? target.href : "/" },
      });
    })()
  );
});

// Een tik op de melding brengt de app weer in beeld, of opent haar als ze dicht is.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url ? event.notification.data.url : "/";
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
      if (open) return open.focus();
      return self.clients.openWindow(url);
    })()
  );
});
