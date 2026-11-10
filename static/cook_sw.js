// De service worker van MealieShopper, alleen voor de meldingen van de kooktimers (static/cook_timers.js, issue #309): op
// Android en in een geïnstalleerde app kan een pagina zelf geen melding tonen, een service worker wel. Hij vangt geen
// verzoeken af (er is geen fetch-handler) en bewaart niets: de app werkt precies zoals zonder.
self.addEventListener("install", () => self.skipWaiting());

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
