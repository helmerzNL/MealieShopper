// Een groot bestand (een back-up, een prijsarchief) in stukken naar de server: mealieshopper/uploads.py.
// Eén verzoek van 100 MB komt niet altijd aan: een reverse proxy, tunnel of CDN voor de app (Cloudflare laat op de gratis
// versie hoogstens 100 MB door) kan het weigeren of nooit doorgeven, en dan blijft de balk staan zonder dat iemand zegt
// waarom. Stukken van een paar MB komen overal door. Een stuk dat te lang op een antwoord wacht of mislukt, gaat opnieuw;
// de server zegt bij elk stuk hoeveel hij al heeft, dus er raakt niets dubbel of kwijt. Laadt na app.js (t, ...).

const UPLOAD_MIN_CHUNK = 256 * 1024;
const UPLOAD_CHUNK_TIMEOUT_MS = 120 * 1000; // een stuk van 4 MB heeft zelfs over een trage verbinding ruim genoeg
const UPLOAD_STALL_MS = 30 * 1000; // zo lang mag er bij een stuk niets gebeuren (de server geeft het na 30 s ook op)
const UPLOAD_RETRIES = 4;

// Eén verzoek; geeft { status, data } (data is {} als het antwoord geen JSON is, zoals de foutpagina van een proxy).
// Een weggevallen verbinding, een verzoek dat te lang duurt of (met `stall`) een stuk waar zo lang niets mee gebeurt,
// wordt een fout met `retry`. Zo wacht het scherm niet twee minuten op een stuk dat onderweg is blijven steken.
function uploadRequest(method, url, { body = null, json = false, timeout = 0, stall = 0, onProgress = null } = {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    let lastActivity = Date.now();
    let watchdog = null;
    const stop = () => clearInterval(watchdog);
    const failed = (key) => () => {
      stop();
      reject(Object.assign(new Error(t(key)), { retry: true }));
    };
    request.open(method, url);
    request.setRequestHeader("Content-Type", json ? "application/json" : "application/octet-stream");
    request.timeout = timeout;
    request.upload.onprogress = (event) => {
      lastActivity = Date.now();
      if (onProgress && event.lengthComputable) onProgress(event.loaded);
    };
    request.upload.onload = () => {
      lastActivity = Date.now(); // alles is verstuurd; nu wachten op het antwoord
    };
    request.onerror = failed("err.generic");
    request.ontimeout = failed("upload.stalled");
    request.onabort = failed("upload.stalled");
    request.onload = () => {
      stop();
      let data = {};
      try {
        data = JSON.parse(request.responseText) || {};
      } catch (error) {
        data = {};
      }
      resolve({ status: request.status, data });
    };
    if (stall) {
      watchdog = setInterval(() => {
        if (Date.now() - lastActivity > stall) request.abort();
      }, 1000);
    }
    request.send(json ? JSON.stringify(body) : body);
  });
}

function uploadFailure({ status, data }) {
  const hint = [413, 502, 504, 520, 524].includes(status) ? t("err.proxy_hint") : "";
  return new Error(data.error || t("err.server", { status, hint }));
}

const uploadOk = (status) => status >= 200 && status < 300;
const uploadWait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Stuurt `file` in stukken naar `route` en laat de server het daarna controleren (`route/<token>/stage`); geeft wat die
// controle teruggeeft. `onProgress(verstuurd, totaal)` volgt de stukken; `onChecking()` zegt dat alles binnen is.
async function uploadInChunks(file, route, { onProgress = () => {}, onChecking = () => {} } = {}) {
  const begun = await uploadRequest("POST", route, { body: { size: file.size }, json: true });
  if (!uploadOk(begun.status)) throw uploadFailure(begun);
  const token = begun.data.token;
  let chunk = Number(begun.data.chunkBytes) || UPLOAD_MIN_CHUNK;
  let offset = 0;
  let failures = 0;
  try {
    onProgress(0, file.size);
    while (offset < file.size) {
      let result;
      try {
        result = await uploadRequest("PUT", `${route}/${token}?offset=${offset}`, {
          body: file.slice(offset, offset + chunk),
          timeout: UPLOAD_CHUNK_TIMEOUT_MS,
          stall: UPLOAD_STALL_MS,
          onProgress: (loaded) => onProgress(Math.min(offset + loaded, file.size), file.size),
        });
      } catch (error) {
        result = { error };
      }
      const { status, data } = result;
      if (status === 409 && Number.isInteger(data.received)) {
        offset = data.received; // de server had dit stuk al (het antwoord ging verloren) of heeft minder: verder waar hij is
        continue;
      }
      if (status === 413 && !String(data.code || "").startsWith("upload_") && chunk > UPLOAD_MIN_CHUNK) {
        chunk = Math.max(UPLOAD_MIN_CHUNK, Math.floor(chunk / 2)); // een proxy weigert zelfs dit stuk: kleinere stukken
        continue;
      }
      if (result.error || [408, 502, 503, 504, 520, 522, 524].includes(status)) {
        // 408: het stuk bleef onderweg steken en de server gaf het op; de rest: een proxy die even niet kon
        failures += 1;
        if (failures > UPLOAD_RETRIES) throw result.error || uploadFailure(result);
        await uploadWait(1000 * 2 ** (failures - 1));
        continue;
      }
      if (!uploadOk(status)) throw uploadFailure(result);
      offset = Number(data.received) || offset + chunk;
      failures = 0;
    }
  } catch (error) {
    fetch(`${route}/${token}`, { method: "DELETE", credentials: "same-origin" }).catch(() => {}); // anders ruimt de server het na een uur op
    throw error;
  }
  onChecking();
  let checked;
  try {
    checked = await uploadRequest("POST", `${route}/${token}/stage`, { body: { size: file.size }, json: true });
  } catch (error) {
    throw new Error(t("err.generic"));
  }
  if (!uploadOk(checked.status)) throw uploadFailure(checked);
  return checked.data;
}
