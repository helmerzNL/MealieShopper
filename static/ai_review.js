// AI-beoordeling van koppelingen (optioneel): de instellingen onder Instellingen > Koppelen en de knop bij de receptvoorstellen.
// Laadt na offers.js en gebruikt daar showOfferRecipes en offersState, en uit app.js de hulpfuncties ($, message, jsonFetch, t, ...).

const AI_REVIEW_PROVIDERS = ["anthropic", "openai"];
const AI_REVIEW_ENV = { anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY" };
const aiReviewState = { status: null, unjudged: 0, running: false };

// Het tabblad Recepten bij Aanbiedingen is zichtbaar met een AI-sleutel (of de omgevingsvariabele); na het opslaan van een sleutel zonder herladen.
function setOffersRecipesTab(visible) {
  const tab = document.querySelector('.subtab[data-subtab="offers-suggestions"]');
  if (!tab) return;
  tab.hidden = !visible;
  if (!visible && tab.classList.contains("is-active")) document.querySelector('.subtab[data-subtab="offers-items"]')?.click();
}

function aiReviewStatusText(info, provider) {
  if (!info.configured) return t("aireview.status.none");
  if (info.source === "env") return t("aireview.status.env", { hint: info.hint, env: AI_REVIEW_ENV[provider] });
  return t("aireview.status.app", { hint: info.hint });
}

function applyAiReviewStatus(status) {
  aiReviewState.status = status;
  $("#ai-provider").value = status.chosen || "";
  for (const provider of AI_REVIEW_PROVIDERS) {
    const info = status.providers[provider];
    $(`#ai-status-${provider}`).textContent = aiReviewStatusText(info, provider);
    $(`#ai-model-${provider}`).value = info.model;
    $(`#ai-model-${provider}`).placeholder = info.defaultModel;
    $(`#ai-clear-${provider}`).hidden = info.source !== "app";
  }
  setOffersRecipesTab(status.recipesTab);
}

async function loadAiReviewSettings() {
  try {
    applyAiReviewStatus(await jsonFetch("/api/offers/ai"));
  } catch (error) {
    message("#ai-review-message", error.message);
  }
}

// Slaat de keuze, de modellen en de ingevulde sleutels op; de sleutelvelden worden daarna leeg gemaakt en de sleutel komt nooit meer terug.
async function saveAiReview(extra = {}) {
  const keys = {};
  const models = {};
  for (const provider of AI_REVIEW_PROVIDERS) {
    const key = $(`#ai-key-${provider}`).value.trim();
    if (key) keys[provider] = key;
    models[provider] = $(`#ai-model-${provider}`).value.trim();
  }
  const status = await jsonFetch("/api/offers/ai", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: $("#ai-provider").value || "auto", models, keys, ...extra }),
  });
  for (const provider of AI_REVIEW_PROVIDERS) $(`#ai-key-${provider}`).value = "";
  applyAiReviewStatus(status);
  return status;
}

async function aiReviewSubmit(event) {
  event.preventDefault();
  message("#ai-review-message", "");
  const button = $("#ai-save");
  setBusy(button, true);
  try {
    await saveAiReview();
    message("#ai-review-message", t("aireview.saved"), "success");
  } catch (error) {
    message("#ai-review-message", error.message);
  } finally {
    setBusy(button, false);
  }
}

async function aiReviewTest(provider) {
  const button = $(`#ai-test-${provider}`);
  message("#ai-review-message", "");
  setBusy(button, true, t("aireview.testing"));
  try {
    await saveAiReview();
    const result = await jsonFetch("/api/offers/ai/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    message("#ai-review-message", t("aireview.test_ok", { name: result.name, model: result.model, tokens: I18N.formatNumber(result.tokens) }), "success");
  } catch (error) {
    message("#ai-review-message", error.message);
  } finally {
    setBusy(button, false, t("aireview.testing"));
  }
}

async function aiReviewClear(provider) {
  message("#ai-review-message", "");
  try {
    await saveAiReview({ clear: [provider] });
    message("#ai-review-message", t("aireview.saved"), "success");
  } catch (error) {
    message("#ai-review-message", error.message);
  }
}

// De regel en de knop boven de receptvoorstellen. De status komt alleen voor de beheerder binnen; voor een gewone gebruiker blijft de balk leeg.
async function renderOfferAi(ai) {
  if (!aiReviewState.status) {
    try {
      aiReviewState.status = await jsonFetch("/api/offers/ai");
    } catch {
      return;
    }
  }
  const status = aiReviewState.status;
  const text = $("#offers-ai-status");
  const button = $("#offers-ai-run");
  aiReviewState.unjudged = ai ? ai.unjudged : 0;
  if (!status.active || !ai) {
    text.textContent = "";
    button.hidden = true;
    return;
  }
  const total = ai.judged + ai.unjudged;
  text.textContent = ai.judged ? t("aireview.progress", { judged: ai.judged, total }) : t("aireview.progress_none");
  button.hidden = !ai.unjudged;
}

async function runAiReview() {
  const status = aiReviewState.status;
  if (!status || !status.active || aiReviewState.running) return;
  const info = status.providers[status.active];
  const count = Math.min(aiReviewState.unjudged, status.defaultMaxPairs);
  if (!window.confirm(t("aireview.confirm", { count, name: info.name }))) return;
  const button = $("#offers-ai-run");
  aiReviewState.running = true;
  button.disabled = true;
  button.innerHTML = `${mdiIcon(MDI_LOADING, "ai-run__icon")}<span>${escapeHtml(t("aireview.running"))}</span>`;
  let notice = null;
  try {
    const result = await jsonFetch("/api/offers/ai/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: offersState.store, max: status.defaultMaxPairs }),
    });
    const done = t("aireview.done", {
      judged: result.judged, ok: result.ok, wrong: result.wrong, remaining: result.remaining,
      tokens: I18N.formatNumber(result.tokens), name: info.name, model: result.model,
    });
    notice = result.error ? { text: `${done} ${result.error}`, type: "error" } : { text: result.judged ? done : t("aireview.nothing"), type: "success" };
  } catch (error) {
    notice = { text: error.message, type: "error" };
  } finally {
    aiReviewState.running = false;
    button.disabled = false;
    button.textContent = t("aireview.run");
  }
  await showOfferRecipes();
  message("#offers-recipes-message", notice.text, notice.type);
}

$("#ai-review-form").addEventListener("submit", aiReviewSubmit);
for (const provider of AI_REVIEW_PROVIDERS) {
  $(`#ai-test-${provider}`).addEventListener("click", () => aiReviewTest(provider));
  $(`#ai-clear-${provider}`).addEventListener("click", () => aiReviewClear(provider));
}
$("#offers-ai-run").addEventListener("click", runAiReview);
window.addEventListener("ms:languagechange", () => {
  if (aiReviewState.status) applyAiReviewStatus(aiReviewState.status);
});
