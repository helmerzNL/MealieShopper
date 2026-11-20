// ---- Het weekmenu bewerken: met de knop naast Synchroniseren (#plan-edit-mode) sleep je een maaltijd naar een andere dag.
// Houd je haar even op de pijl naar de vorige of volgende week (#prev-week, #next-week), dan schuift het weekmenu een week op
// (en zo lang je blijft staan nog een week), zodat je haar in een andere week neerzet. Het slepen gebruikt pointer-events, zodat
// het met een muis, een vinger en een pen hetzelfde werkt; HTML5 drag-and-drop werkt op een telefoon niet. Neerzetten gaat
// meteen naar Mealie (PATCH /api/mealplan/<id> met alleen de nieuwe dag); de rest van de maaltijd blijft zoals hij was.

// Pas na zoveel pixels beweging is het slepen; minder is een tik die niets doet.
const PLAN_DRAG_THRESHOLD_PX = 6;
// Zo lang blijft een maaltijd op een pijl staan voor het weekmenu een week opschuift, en daarna voor elke volgende week.
const PLAN_DRAG_WEEK_DWELL_MS = 600;
const PLAN_DRAG_WEEK_REPEAT_MS = 1000;
// Dichter dan dit bij de boven- of onderrand van het venster scrolt de pagina mee, zodat je op een telefoon bij de pijlen komt.
const PLAN_DRAG_SCROLL_EDGE_PX = 64;
const PLAN_DRAG_SCROLL_MAX_PX = 18;

const planDrag = {
  pointerId: null, entry: null, source: null, ghost: null, started: false,
  startX: 0, startY: 0, x: 0, y: 0, grabX: 0, grabY: 0,
  target: null, weekButton: null, weekTimer: 0, scrollFrame: 0,
};

function planArranging() {
  return $("#meal-days").classList.contains("is-arranging");
}

function setPlanArranging(on) {
  if (!on) planDragStop();
  $("#plan-edit-mode").setAttribute("aria-pressed", String(on));
  $("#meal-days").classList.toggle("is-arranging", on);
  $("#plan-arrange-hint").hidden = !on;
}

// De week na het verplaatsen, nog voor Mealie antwoordt: staat de nieuwe dag in de getoonde week, dan staat de maaltijd daar;
// anders verdwijnt ze uit deze week. Een nieuwe lijst: de oude hoort bij de bewaarde week (mealPlanCache) en blijft zo.
function planEntriesAfterMove(entries, id, date, weekDays) {
  const inWeek = weekDays.includes(date);
  return entries.flatMap((entry) => (String(entry.id) !== String(id) ? [entry] : inWeek ? [{ ...entry, date }] : []));
}

async function movePlanEntry(entry, date) {
  const from = entry.date;
  if (!date || date === from) return;
  const week = getWeekBounds(state.weekOffset);
  state.entries = planEntriesAfterMove(state.entries || [], entry.id, date, week.days);
  renderMealPlan(state.entries);
  try {
    await planRequest(`/api/mealplan/${encodeURIComponent(entry.id)}`, "PATCH", { date });
    toast(t("plan.moved", { name: mealName(entry), day: formatDate(date) }));
  } catch (error) {
    message("#planner-message", t("plan.move_failed", { error: error.message }));
  }
  // Ook na een fout: dan staat de maaltijd weer waar Mealie haar heeft.
  refreshMealPlanAfterChange([from, date]);
}

function planDragBegin(event) {
  if (!planArranging() || planDrag.pointerId != null) return;
  if (event.pointerType === "mouse" && event.button !== 0) return;
  const meal = event.target.closest(".meal--planned[data-plan-entry]");
  if (!meal) return;
  const entry = (state.entries || []).find((item) => String(item.id) === meal.dataset.planEntry);
  if (!entry) return;
  Object.assign(planDrag, {
    pointerId: event.pointerId, entry, source: meal, started: false,
    startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY,
  });
  event.preventDefault(); // geen tekst selecteren en geen focusring tijdens het slepen
}

function planDragStart() {
  const rect = planDrag.source.getBoundingClientRect();
  const ghost = planDrag.source.cloneNode(true);
  ghost.classList.add("meal-ghost");
  ghost.removeAttribute("data-plan-entry");
  ghost.setAttribute("aria-hidden", "true");
  ghost.querySelectorAll("button").forEach((button) => button.setAttribute("tabindex", "-1"));
  ghost.style.width = `${rect.width}px`;
  planDrag.grabX = planDrag.startX - rect.left;
  planDrag.grabY = planDrag.startY - rect.top;
  document.body.append(ghost);
  planDrag.ghost = ghost;
  planDrag.started = true;
  planDrag.source.classList.add("is-dragging");
  document.body.classList.add("is-plan-dragging");
  planDragScroll();
}

function planDragMove(event) {
  if (event.pointerId !== planDrag.pointerId) return;
  planDrag.x = event.clientX;
  planDrag.y = event.clientY;
  if (!planDrag.started) {
    if (Math.hypot(planDrag.x - planDrag.startX, planDrag.y - planDrag.startY) < PLAN_DRAG_THRESHOLD_PX) return;
    planDragStart();
  }
  event.preventDefault();
  planDragUpdate();
}

// Waar de maaltijd nu boven hangt: een dag (oplichten) of een pijl naar een andere week (na even wachten een week verder).
function planDragUpdate() {
  if (!planDrag.started) return;
  planDrag.ghost.style.transform = `translate(${planDrag.x - planDrag.grabX}px, ${planDrag.y - planDrag.grabY}px)`;
  const under = document.elementFromPoint(planDrag.x, planDrag.y);
  const day = under?.closest("#meal-days [data-plan-date]") || null;
  const target = day && day.dataset.planDate !== planDrag.entry.date ? day : null;
  if (target !== planDrag.target) {
    planDrag.target?.classList.remove("is-drop-target");
    target?.classList.add("is-drop-target");
    planDrag.target = target;
  }
  planDragWeekHover(under?.closest("#prev-week, #next-week") || null);
}

function planDragWeekHover(button) {
  if (button === planDrag.weekButton) return;
  planDrag.weekButton?.classList.remove("is-drop-hover");
  clearTimeout(planDrag.weekTimer);
  planDrag.weekButton = button;
  if (!button) return;
  button.classList.add("is-drop-hover");
  const step = () => {
    if (planDrag.weekButton !== button) return;
    changePlanWeek(state.weekOffset + (button.id === "next-week" ? 1 : -1));
    planDrag.target = null; // de dagen zijn opnieuw getekend
    planDragUpdate();
    planDrag.weekTimer = setTimeout(step, PLAN_DRAG_WEEK_REPEAT_MS);
  };
  planDrag.weekTimer = setTimeout(step, PLAN_DRAG_WEEK_DWELL_MS);
}

// Bij de rand van het venster scrolt de pagina mee, sneller naarmate de maaltijd dichter bij de rand is.
function planDragScroll() {
  if (!planDrag.started) return;
  const edge = PLAN_DRAG_SCROLL_EDGE_PX;
  const { y } = planDrag;
  const speed = y < edge ? -(edge - y) / edge : y > innerHeight - edge ? (y - (innerHeight - edge)) / edge : 0;
  if (speed) {
    window.scrollBy(0, Math.round(Math.max(-1, Math.min(1, speed)) * PLAN_DRAG_SCROLL_MAX_PX));
    planDragUpdate();
  }
  planDrag.scrollFrame = requestAnimationFrame(planDragScroll);
}

function planDragEnd(event) {
  if (event.pointerId !== planDrag.pointerId) return;
  const { started, target, entry } = planDrag;
  const date = event.type === "pointerup" && started && target ? target.dataset.planDate : "";
  planDragStop();
  if (date) movePlanEntry(entry, date);
}

function planDragStop() {
  cancelAnimationFrame(planDrag.scrollFrame);
  clearTimeout(planDrag.weekTimer);
  planDrag.ghost?.remove();
  planDrag.source?.classList.remove("is-dragging");
  planDrag.target?.classList.remove("is-drop-target");
  planDrag.weekButton?.classList.remove("is-drop-hover");
  document.body.classList.remove("is-plan-dragging");
  Object.assign(planDrag, { pointerId: null, entry: null, source: null, ghost: null, started: false, target: null, weekButton: null });
}

$("#plan-edit-mode").addEventListener("click", () => setPlanArranging(!planArranging()));
$("#meal-days").addEventListener("pointerdown", planDragBegin);
// Een plaatje sleept de browser anders zelf mee (en dan stoppen de pointer-events).
$("#meal-days").addEventListener("dragstart", (event) => {
  if (planArranging()) event.preventDefault();
});
document.addEventListener("pointermove", planDragMove, { passive: false });
document.addEventListener("pointerup", planDragEnd);
document.addEventListener("pointercancel", planDragEnd);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && planDrag.pointerId != null) planDragStop();
});
