// AI-beoordeling van koppelingen (optioneel): de instellingen onder Instellingen > Koppelen en de knop bij de receptvoorstellen.
// Laadt na offers.js en gebruikt daar showOfferRecipes en offersState, en uit app.js de hulpfuncties ($, message, jsonFetch, t, ...).

const AI_REVIEW_PROVIDERS = ["anthropic", "openai"];
const AI_REVIEW_ENV = { anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY" };
const AI_SEARCH_POLL_MS = 2000;
const aiReviewState = { status: null, running: false, timer: 0 };

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
  applyStoreFold("ai", AI_REVIEW_PROVIDERS.some((provider) => status.providers[provider].configured));
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
  if (!status.active || !ai) {
    text.textContent = "";
    button.hidden = true;
    return;
  }
  if (!aiReviewState.running) {
    text.textContent = ai.searched ? t("aisearch.searched", { searched: ai.searched, recipes: ai.recipes }) : t("aisearch.searched_none");
    button.hidden = false;
  }
  // Loopt er een ronde (ook na een herlading van de pagina), dan tonen we de voortgang.
  if (status.search && status.search.state === "running" && !aiReviewState.running) watchAiSearch(status.search);
}

function setAiSearchBusy(job) {
  const button = $("#offers-ai-run");
  button.disabled = true;
  button.hidden = false;
  button.innerHTML = `${mdiIcon(MDI_LOADING, "ai-run__icon")}<span>${escapeHtml(t("aisearch.progress", { done: job.done || 0, total: job.total || 0 }))}</span>`;
}

function aiSearchNotice(job, info) {
  const result = job.result || {};
  const done = t("aisearch.done", {
    searched: result.searched || 0, matched: result.matched || 0, remaining: result.remaining || 0,
    tokens: I18N.formatNumber(result.tokens || job.tokens || 0), name: info.name, model: result.model || info.model,
  });
  if (job.state === "error") return { text: result.searched ? `${done} ${job.error}` : job.error, type: "error" };
  return { text: done, type: "success" };
}

// Volgt een lopende ronde: elke twee seconden de stand ophalen, en aan het eind de lijst vernieuwen en een melding tonen.
function watchAiSearch(job) {
  const status = aiReviewState.status;
  const info = status.providers[status.active] || { name: "", model: "" };
  aiReviewState.running = true;
  setAiSearchBusy(job);
  clearInterval(aiReviewState.timer);
  const tick = async () => {
    let current;
    try {
      current = await jsonFetch("/api/offers/ai/search/status");
    } catch (error) {
      current = { state: "error", error: error.message };
    }
    if (current.state === "running") {
      setAiSearchBusy(current);
      return;
    }
    clearInterval(aiReviewState.timer);
    aiReviewState.running = false;
    $("#offers-ai-run").disabled = false;
    $("#offers-ai-run").textContent = t("aireview.run");
    const notice = aiSearchNotice(current, info);
    status.search = current;
    await showOfferRecipes();
    message("#offers-recipes-message", notice.text, notice.type);
  };
  aiReviewState.timer = setInterval(tick, AI_SEARCH_POLL_MS);
}

async function runAiSearch() {
  const status = aiReviewState.status;
  if (!status || !status.active || aiReviewState.running) return;
  const info = status.providers[status.active];
  message("#offers-recipes-message", "");
  try {
    const plan = await jsonFetch(`/api/offers/ai/search/plan?store=${encodeURIComponent(offersState.store)}`);
    if (!plan.todo) {
      message("#offers-recipes-message", t("aisearch.nothing"), "success");
      return;
    }
    if (!window.confirm(t("aisearch.confirm", { recipes: plan.chosen, name: info.name, tokens: I18N.formatNumber(plan.estimatedTokens) }))) return;
    const job = await jsonFetch("/api/offers/ai/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ store: offersState.store, max: plan.maxRecipes }),
    });
    watchAiSearch(job);
  } catch (error) {
    message("#offers-recipes-message", error.message);
  }
}

$("#ai-review-form").addEventListener("submit", aiReviewSubmit);
for (const provider of AI_REVIEW_PROVIDERS) {
  $(`#ai-test-${provider}`).addEventListener("click", () => aiReviewTest(provider));
  $(`#ai-clear-${provider}`).addEventListener("click", () => aiReviewClear(provider));
}
$("#offers-ai-run").addEventListener("click", runAiSearch);
window.addEventListener("ms:languagechange", () => {
  if (aiReviewState.status) applyAiReviewStatus(aiReviewState.status);
});
