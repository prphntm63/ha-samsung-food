/**
 * Samsung Food meal plan card.
 *
 * Reads the `meals` / `next_week_meals` attributes of the Samsung Food meal plan
 * sensor and renders one of three layouts:
 *   - week:   7 day columns with meal tiles (good for large/portrait screens)
 *   - agenda: one row per day with larger recipe cards
 *   - next:   compact card with the next meal(s) still to come today
 *
 * Tapping a meal opens its recipe (`link`: original site, else Samsung Food) in a new tab.
 */

const CARD_TAG = "samsung-food-meal-plan-card";
const DEFAULT_ENTITY = "sensor.samsung_food_meal_plan";
// Meal times that count as a "time slot", in the order they happen.
const SLOTS = ["Breakfast", "Lunch", "Dinner"];
const MEAL_ORDER = { Breakfast: 0, Lunch: 1, Dinner: 2, Snack: 3 };

const DEFAULTS = {
  entity: DEFAULT_ENTITY,
  layout: "week",
  week: "this",
  title: "",
  show_images: true,
  show_past_days: true,
  show_source: true,
  breakfast_until: "11:00",
  lunch_until: "15:00",
};

// ------------------------------------------------------------------ helpers

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseDate = (iso) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};
const addDays = (iso, n) => {
  const d = parseDate(iso);
  d.setDate(d.getDate() + n);
  return isoDate(d);
};

/** "11:00", "11:00:00" or 11 -> minutes after midnight. */
const toMinutes = (value, fallback) => {
  if (typeof value === "number") return value * 60;
  const m = /^(\d{1,2})(?::(\d{2}))?/.exec(String(value ?? ""));
  return m ? Number(m[1]) * 60 + Number(m[2] || 0) : fallback;
};

const byMealTime = (a, b) => (MEAL_ORDER[a.meal_time] ?? 9) - (MEAL_ORDER[b.meal_time] ?? 9);

/**
 * Pick which of today's meals the "next" layout shows.
 *  - Breakfast is shown until `breakfast_until`, lunch until `lunch_until`, dinner for the rest of the day.
 *  - The earliest slot that still has meals wins, and every meal in that slot is shown.
 *  - Meals without a slot (snacks, unlabelled meals) are only shown when no slotted meal is left.
 * Returns { label, meals } or null when nothing is left today.
 */
export function pickNextMeals(meals, now, config = {}) {
  const today = isoDate(now);
  const minutes = now.getHours() * 60 + now.getMinutes();
  const cutoffs = {
    Breakfast: toMinutes(config.breakfast_until, 11 * 60),
    Lunch: toMinutes(config.lunch_until, 15 * 60),
    Dinner: 24 * 60,
  };
  const todays = (meals || []).filter((m) => m.date === today);
  for (const slot of SLOTS) {
    if (minutes >= cutoffs[slot]) continue;
    const inSlot = todays.filter((m) => m.meal_time === slot);
    if (inSlot.length) return { label: slot, meals: inSlot };
  }
  const unslotted = todays.filter((m) => !SLOTS.includes(m.meal_time)).sort(byMealTime);
  if (unslotted.length) {
    const labels = [...new Set(unslotted.map((m) => m.meal_time))];
    return { label: labels.length === 1 ? labels[0] : "Today", meals: unslotted };
  }
  return null;
}

// ------------------------------------------------------------------ card

class SamsungFoodMealPlanCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = { ...DEFAULTS };
    this._renderKey = "";
  }

  static getStubConfig(hass) {
    const entity =
      Object.keys(hass?.states || {}).find(
        (id) => id.startsWith("sensor.") && Array.isArray(hass.states[id].attributes?.meals)
      ) || DEFAULT_ENTITY;
    return { entity, layout: "week" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { domain: "sensor", integration: "samsung_food" } } },
        {
          name: "layout",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "week", label: "Week strip" },
                { value: "agenda", label: "Agenda list" },
                { value: "next", label: "Next meal (today)" },
              ],
            },
          },
        },
        { name: "title", selector: { text: {} } },
        {
          type: "grid",
          name: "",
          schema: [
            {
              name: "week",
              selector: {
                select: {
                  mode: "dropdown",
                  options: [
                    { value: "this", label: "This week" },
                    { value: "next", label: "Next week" },
                  ],
                },
              },
            },
            { name: "show_images", selector: { boolean: {} } },
            { name: "show_past_days", selector: { boolean: {} } },
            { name: "show_source", selector: { boolean: {} } },
          ],
        },
        {
          type: "expandable",
          name: "",
          title: "Next meal timing",
          schema: [
            {
              type: "grid",
              name: "",
              schema: [
                { name: "breakfast_until", selector: { time: {} } },
                { name: "lunch_until", selector: { time: {} } },
              ],
            },
          ],
        },
      ],
      computeLabel: (s) =>
        ({
          entity: "Meal plan sensor",
          layout: "Layout",
          title: "Title (optional)",
          week: "Week (week strip / agenda)",
          show_images: "Show recipe images",
          show_past_days: "Show earlier days this week",
          show_source: "Show recipe source",
          breakfast_until: "Show breakfast until",
          lunch_until: "Show lunch until",
        })[s.name],
    };
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    if (config.layout && !["week", "agenda", "next"].includes(config.layout)) {
      throw new Error("layout must be week, agenda or next");
    }
    this._config = { ...DEFAULTS, ...config };
    this._renderKey = "";
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // Re-render every minute so "today" and the next-meal cutoffs advance without a state change.
    this._timer = setInterval(() => this._render(), 60 * 1000);
    this._render();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return { next: 2, agenda: 8, week: 6 }[this._config.layout] ?? 6;
  }

  getGridOptions() {
    return this._config.layout === "next"
      ? { columns: 6, min_columns: 4, rows: "auto" }
      : { columns: "full", min_columns: 6, rows: "auto" };
  }

  // ---------------------------------------------------------------- rendering

  _render() {
    if (!this.shadowRoot) return;
    const cfg = this._config;
    const stateObj = this._hass?.states?.[cfg.entity];
    const now = new Date();
    // Only touch the DOM when something visible could have changed.
    const key = JSON.stringify([
      cfg, !!this._hass, !!stateObj, stateObj?.last_updated, stateObj?.state, isoDate(now), now.getHours(), now.getMinutes(),
    ]);
    if (key === this._renderKey) return;
    this._renderKey = key;

    let body;
    if (!this._hass) {
      body = "";
    } else if (!stateObj) {
      body = `<div class="notice">Entity not found: ${esc(cfg.entity)}</div>`;
    } else if (cfg.layout === "next") {
      body = this._renderNext(stateObj, now);
    } else {
      const days = this._days(stateObj, now);
      body = cfg.layout === "agenda" ? this._renderAgenda(days, now) : this._renderWeek(days, now);
    }
    const title = cfg.title ? `<h1 class="title">${esc(cfg.title)}</h1>` : "";
    this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card class="layout-${esc(cfg.layout)}">${title}${body}</ha-card>`;
  }

  _locale() {
    return this._hass?.locale?.language || this._hass?.language || undefined;
  }

  _days(stateObj, now) {
    const attrs = stateObj.attributes || {};
    const next = this._config.week === "next";
    const meals = (next ? attrs.next_week_meals : attrs.meals) || [];
    let start = attrs.week_start || (meals[0] && meals[0].date) || isoDate(now);
    if (next) start = addDays(start, 7);
    const today = isoDate(now);
    const days = [];
    for (let i = 0; i < 7; i++) {
      const date = addDays(start, i);
      if (!this._config.show_past_days && date < today) continue;
      days.push({
        date,
        isToday: date === today,
        isPast: date < today,
        meals: meals.filter((m) => m.date === date).sort(byMealTime),
      });
    }
    return days;
  }

  _dayLabel(date, opts) {
    return parseDate(date).toLocaleDateString(this._locale(), opts);
  }

  _image(meal, cls) {
    if (!this._config.show_images) return "";
    return meal.image
      ? `<div class="img ${cls}"><img src="${esc(meal.image)}" alt="" loading="lazy" referrerpolicy="no-referrer"></div>`
      : `<div class="img ${cls} placeholder"><ha-icon icon="mdi:silverware-fork-knife"></ha-icon></div>`;
  }

  _source(meal) {
    return this._config.show_source && meal.source ? `<span class="source">${esc(meal.source)}</span>` : "";
  }

  _link(meal, cls, inner) {
    const href = meal.link || meal.url;
    return href
      ? `<a class="${cls}" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${inner}</a>`
      : `<div class="${cls}">${inner}</div>`;
  }

  _renderWeek(days, now) {
    const cols = days
      .map((day) => {
        const meals = day.meals.length
          ? day.meals
              .map((m) =>
                this._link(
                  m,
                  "tile",
                  `${this._image(m, "square")}<div class="tile-text"><span class="chip">${esc(m.meal_time)}</span><span class="name">${esc(m.name)}</span>${this._source(m)}</div>`
                )
              )
              .join("")
          : `<div class="empty-day">Nothing planned</div>`;
        return `
          <section class="day ${day.isToday ? "today" : ""} ${day.isPast ? "past" : ""}">
            <header>
              <span class="dow">${esc(day.isToday ? "Today" : this._dayLabel(day.date, { weekday: "short" }))}</span>
              <span class="dom">${parseDate(day.date).getDate()}</span>
            </header>
            <div class="meals">${meals}</div>
          </section>`;
      })
      .join("");
    return `<div class="week" style="--days:${days.length || 1}">${cols}</div>`;
  }

  _renderAgenda(days, now) {
    if (!days.length) return `<div class="notice">Nothing left this week.</div>`;
    return `<div class="agenda">${days
      .map((day) => {
        const meals = day.meals.length
          ? day.meals
              .map((m) =>
                this._link(
                  m,
                  "row-card",
                  `${this._image(m, "wide")}<div class="row-text"><span class="chip">${esc(m.meal_time)}</span><span class="name">${esc(m.name)}</span>${this._source(m)}</div><ha-icon class="open" icon="mdi:open-in-new"></ha-icon>`
                )
              )
              .join("")
          : `<div class="empty-day">Nothing planned</div>`;
        return `
          <section class="agenda-day ${day.isToday ? "today" : ""} ${day.isPast ? "past" : ""}">
            <div class="date">
              <span class="dom">${parseDate(day.date).getDate()}</span>
              <span class="dow">${esc(day.isToday ? "Today" : this._dayLabel(day.date, { weekday: "long" }))}</span>
            </div>
            <div class="agenda-meals">${meals}</div>
          </section>`;
      })
      .join("")}</div>`;
  }

  _renderNext(stateObj, now) {
    const pick = pickNextMeals(stateObj.attributes?.meals, now, this._config);
    if (!pick) {
      return `<div class="next none"><ha-icon icon="mdi:silverware-clean"></ha-icon><div><div class="next-label">Today</div><div class="name">No More Meals</div></div></div>`;
    }
    const items = pick.meals
      .map((m) =>
        this._link(
          m,
          "next-item",
          `${this._image(m, "thumb")}<div class="row-text"><span class="name">${esc(m.name)}</span>${this._source(m)}</div><ha-icon class="open" icon="mdi:open-in-new"></ha-icon>`
        )
      )
      .join("");
    return `<div class="next"><div class="next-label">Next up · ${esc(pick.label)}</div>${items}</div>`;
  }
}

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; overflow: hidden; container-type: inline-size; }
  .title { margin: 0 0 12px; font-size: 1.5rem; font-weight: 500; color: var(--primary-text-color); }
  a { color: inherit; text-decoration: none; -webkit-tap-highlight-color: transparent; }
  .notice, .empty-day { color: var(--secondary-text-color); }
  .empty-day { font-size: 0.85rem; padding: 8px 2px; opacity: 0.8; }
  .name { font-weight: 500; color: var(--primary-text-color); line-height: 1.25; }
  .source { font-size: 0.75rem; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip {
    align-self: flex-start; font-size: 0.68rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--primary-color);
  }
  .img { overflow: hidden; background: var(--secondary-background-color, rgba(127,127,127,0.12)); flex: none; }
  .img img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .img.placeholder { display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color); }
  .open { color: var(--secondary-text-color); --mdc-icon-size: 20px; flex: none; }
  .past { opacity: 0.5; }

  /* ---- week strip ---- */
  .week { display: grid; grid-template-columns: repeat(var(--days), minmax(0, 1fr)); gap: 8px; }
  .day { display: flex; flex-direction: column; gap: 8px; min-width: 0; padding: 8px 6px; border-radius: 12px; }
  .day header { display: flex; flex-direction: column; align-items: center; gap: 2px; padding-bottom: 6px; border-bottom: 1px solid var(--divider-color); }
  .day .dow { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--secondary-text-color); }
  .day .dom { font-size: 1.5rem; font-weight: 500; color: var(--primary-text-color); line-height: 1.1; }
  .day.today { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.1); }
  .day.today header { border-bottom-color: var(--primary-color); }
  .day.today .dow, .day.today .dom { color: var(--primary-color); }
  .meals { display: flex; flex-direction: column; gap: 10px; }
  .tile { display: flex; flex-direction: column; border-radius: 10px; overflow: hidden; background: var(--card-background-color); box-shadow: 0 1px 3px rgba(0,0,0,0.12); transition: transform 0.1s; }
  .tile:active { transform: scale(0.97); }
  .tile .img.square { aspect-ratio: 1 / 1; width: 100%; }
  .tile-text { display: flex; flex-direction: column; gap: 3px; padding: 8px; min-width: 0; }
  .tile .name { font-size: 0.85rem; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }

  /* ---- agenda ---- */
  .agenda { display: flex; flex-direction: column; }
  .agenda-day { display: grid; grid-template-columns: 96px 1fr; gap: 16px; padding: 14px 0; border-top: 1px solid var(--divider-color); }
  .agenda-day:first-child { border-top: none; padding-top: 0; }
  .agenda-day .date { display: flex; flex-direction: column; align-items: flex-start; padding-top: 4px; }
  .agenda-day .dom { font-size: 2.2rem; font-weight: 400; line-height: 1; color: var(--primary-text-color); }
  .agenda-day .dow { font-size: 0.85rem; color: var(--secondary-text-color); margin-top: 4px; }
  .agenda-day.today .dom, .agenda-day.today .dow { color: var(--primary-color); font-weight: 500; }
  .agenda-meals { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .row-card, .next-item { display: flex; align-items: center; gap: 14px; padding: 8px; padding-right: 14px; border-radius: 12px; min-width: 0; background: var(--secondary-background-color, rgba(127,127,127,0.08)); transition: transform 0.1s; }
  .row-card:active, .next-item:active { transform: scale(0.98); }
  .row-card .img.wide { width: 128px; height: 88px; border-radius: 8px; }
  .row-text { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
  .row-card .name { font-size: 1.05rem; }
  .agenda-day.today .row-card { box-shadow: inset 3px 0 0 var(--primary-color); }

  /* ---- next meal ---- */
  .next { display: flex; flex-direction: column; gap: 10px; }
  .next-label { font-size: 0.8rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--primary-color); }
  .next-item .img.thumb { width: 72px; height: 72px; border-radius: 10px; }
  .next-item .name { font-size: 1.1rem; }
  .next.none { flex-direction: row; align-items: center; gap: 14px; }
  .next.none ha-icon { --mdc-icon-size: 40px; color: var(--secondary-text-color); }
  .next.none .next-label { color: var(--secondary-text-color); }
  .next.none .name { font-size: 1.2rem; }

  /* Narrow cards: stack the week strip into rows so it stays readable. */
  @container (max-width: 560px) {
    .week { grid-template-columns: 1fr; }
    .day { flex-direction: row; align-items: flex-start; }
    .day header { min-width: 52px; border-bottom: none; border-right: 1px solid var(--divider-color); padding: 0 8px 0 0; }
    .meals { flex: 1; min-width: 0; }
    .tile { flex-direction: row; }
    .tile .img.square { width: 64px; height: 64px; }
    .agenda-day { grid-template-columns: 64px 1fr; gap: 10px; }
    .row-card .img.wide { width: 88px; height: 66px; }
  }
`;

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, SamsungFoodMealPlanCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TAG,
    name: "Samsung Food meal plan",
    description: "This week's meals as a week strip, an agenda, or the next meal today.",
    preview: true,
    documentationURL: "https://app.samsungfood.com/meal-plan",
  });
}
