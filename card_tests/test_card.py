"""Browser tests for the meal plan card (needs: pip install playwright && playwright install chromium).

Run directly to also write screenshots:  python test_card.py <screenshot dir>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

CARD_JS = (
    Path(__file__).resolve().parents[1]
    / "custom_components/samsung_food/frontend/samsung-food-meal-plan.js"
).read_text()

TODAY = "2026-10-15"  # a Thursday; the sample week runs Sun 11 - Sat 17


def _img(color: str) -> str:
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><rect width='200' height='200' fill='{color}'/></svg>"
    return "data:image/svg+xml," + svg.replace("#", "%23").replace("<", "%3C").replace(">", "%3E")


def meal(date: str, meal_time: str, name: str, color: str = "#c98b5a", source: str = "example.com") -> dict:
    return {
        "date": date,
        "weekday": datetime.fromisoformat(date).strftime("%A"),
        "meal_time": meal_time,
        "name": name,
        "url": f"https://app.samsungfood.com/recipes/{abs(hash(name))}",
        "link": f"https://{source}/{name.lower().replace(' ', '-')}",
        "image": _img(color) if color else "",
        "source": source,
        "source_url": f"https://{source}/x",
    }


SAMPLE_WEEK = [
    meal("2026-10-11", "Breakfast", "Classic Sourdough Pancakes or Waffles", "#d9a35f", "kingarthurbaking.com"),
    meal("2026-10-13", "Dinner", "Spicy Chipotle Honey Salmon Bowls", "#d0603c", "halfbakedharvest.com"),
    meal("2026-10-14", "Dinner", "Enchiladas Verdes in a Tomatillo Sauce", "#6f9a4c", "patijinich.com"),
    meal("2026-10-15", "Lunch", "Lemony White Bean Soup", "#e3c565", "smittenkitchen.com"),
    meal("2026-10-15", "Dinner", "Sheet Pan Gnocchi with Sausage", "#b5543c", "budgetbytes.com"),
    meal("2026-10-15", "Dinner", "Garlicky Green Beans", "#5f8f45", "seriouseats.com"),
    meal("2026-10-16", "Dinner", "Homemade Margherita Pizza", "#c7402d", "", ),
    meal("2026-10-17", "Snack", "Apple Crumble Bars", "#c58f4e", "sallysbakingaddiction.com"),
]
SAMPLE_WEEK[6]["link"] = SAMPLE_WEEK[6]["url"]
SAMPLE_WEEK[6]["source"] = ""
SAMPLE_WEEK[6]["image"] = ""  # exercise the no-image placeholder

THEMES = {
    "light": {
        "--primary-color": "#03a9f4", "--rgb-primary-color": "3, 169, 244", "--primary-text-color": "#212121",
        "--secondary-text-color": "#727272", "--card-background-color": "#ffffff", "--divider-color": "rgba(0,0,0,.12)",
        "--secondary-background-color": "#f0f2f5", "--page-bg": "#fafafa",
    },
    "dark": {
        "--primary-color": "#03a9f4", "--rgb-primary-color": "3, 169, 244", "--primary-text-color": "#e1e1e1",
        "--secondary-text-color": "#9b9b9b", "--card-background-color": "#1c1c1c", "--divider-color": "rgba(225,225,225,.12)",
        "--secondary-background-color": "#282828", "--page-bg": "#111111",
    },
}

HARNESS = """<!doctype html><html><head><meta charset="utf-8"><style>
  body { margin: 0; padding: 16px; background: var(--page-bg); font-family: Roboto, -apple-system, 'Segoe UI', sans-serif; }
  .stack { display: grid; gap: 16px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
</style>
<script>
  // Minimal stand-ins for Home Assistant's own elements.
  customElements.define('ha-card', class extends HTMLElement {
    connectedCallback() { Object.assign(this.style, { display: 'block', background: 'var(--card-background-color)',
      borderRadius: '12px', border: '1px solid var(--divider-color)', boxSizing: 'border-box' }); } });
  customElements.define('ha-icon', class extends HTMLElement {
    connectedCallback() { Object.assign(this.style, { display: 'inline-block', width: 'var(--mdc-icon-size, 24px)',
      height: 'var(--mdc-icon-size, 24px)', borderRadius: '50%', background: 'currentColor', opacity: '.35' }); } });
</script></head><body><div class="stack" id="root"></div></body></html>"""


@pytest.fixture(scope="module")
def page():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1080, "height": 1920})
        pg.clock.set_fixed_time(datetime.fromisoformat(f"{TODAY}T12:30:00"))
        pg.set_content(HARNESS)
        pg.add_script_tag(content=CARD_JS, type="module")
        pg.wait_for_function("!!customElements.get('samsung-food-meal-plan-card')")
        pg.evaluate(
            "src => import(URL.createObjectURL(new Blob([src], {type: 'text/javascript'}))).then(m => { window.pickNextMeals = m.pickNextMeals; })",
            CARD_JS,
        )
        yield pg
        browser.close()


def pick(page, meals, time, config=None):
    return page.evaluate(
        """([meals, time, config]) => {
            const r = window.pickNextMeals(meals, new Date(time), config || {});
            return r && { label: r.label, names: r.meals.map(m => m.name) };
        }""",
        [meals, f"{TODAY}T{time}", config],
    )


def m(meal_time, name, date=TODAY):
    return {"date": date, "meal_time": meal_time, "name": name}


DAY = [m("Breakfast", "B"), m("Lunch", "L"), m("Dinner", "D")]


@pytest.mark.parametrize(
    ("time", "expected"),
    [
        ("00:00", ("Breakfast", ["B"])),
        ("10:59", ("Breakfast", ["B"])),
        ("11:00", ("Lunch", ["L"])),
        ("14:59", ("Lunch", ["L"])),
        ("15:00", ("Dinner", ["D"])),
        ("23:59", ("Dinner", ["D"])),
    ],
)
def test_slot_cutoffs(page, time, expected):
    assert pick(page, DAY, time) == {"label": expected[0], "names": expected[1]}


def test_all_meals_in_slot_are_shown(page):
    meals = [m("Dinner", "D1"), m("Snack", "S"), m("Dinner", "D2")]
    assert pick(page, meals, "16:00") == {"label": "Dinner", "names": ["D1", "D2"]}


def test_skips_empty_slots(page):
    assert pick(page, [m("Dinner", "D")], "08:00") == {"label": "Dinner", "names": ["D"]}
    assert pick(page, [m("Lunch", "L"), m("Dinner", "D")], "08:00") == {"label": "Lunch", "names": ["L"]}


def test_slotted_meals_beat_unslotted(page):
    meals = [m("Snack", "S"), m("Meal", "Unlabelled"), m("Lunch", "L")]
    assert pick(page, meals, "09:00") == {"label": "Lunch", "names": ["L"]}


def test_unslotted_meals_when_no_slots_left(page):
    assert pick(page, [m("Breakfast", "B"), m("Snack", "S")], "12:00") == {"label": "Snack", "names": ["S"]}
    assert pick(page, [m("Snack", "S"), m("Meal", "X")], "21:00") == {"label": "Today", "names": ["S", "X"]}


def test_no_more_meals(page):
    assert pick(page, [m("Breakfast", "B"), m("Lunch", "L")], "15:30") is None
    assert pick(page, [], "09:00") is None
    assert pick(page, [m("Dinner", "Tomorrow", "2026-10-16")], "09:00") is None


def test_custom_cutoffs(page):
    cfg = {"breakfast_until": "10:30:00", "lunch_until": "14:00:00"}
    assert pick(page, DAY, "10:29", cfg)["label"] == "Breakfast"
    assert pick(page, DAY, "10:30", cfg)["label"] == "Lunch"
    assert pick(page, DAY, "14:00", cfg)["label"] == "Dinner"


def render(page, theme, cards):
    """cards: list of rows; each row is a list of card configs."""
    page.evaluate(
        """([theme, rows, meals]) => {
            for (const [k, v] of Object.entries(theme)) document.documentElement.style.setProperty(k, v);
            const hass = { language: 'en', locale: { language: 'en' }, states: { 'sensor.samsung_food_meal_plan': {
              state: 'x', last_updated: 'now', attributes: { week_start: '2026-10-11', meals, next_week_meals: [] } } } };
            const root = document.getElementById('root');
            root.innerHTML = '';
            for (const row of rows) {
              const wrap = document.createElement('div');
              if (row.length > 1) wrap.className = 'row';
              for (const cfg of row) {
                const card = document.createElement('samsung-food-meal-plan-card');
                card.setConfig(cfg);
                card.hass = hass;
                wrap.appendChild(card);
              }
              root.appendChild(wrap);
            }
        }""",
        [theme, cards, SAMPLE_WEEK],
    )
    page.wait_for_timeout(100)


def card_text(page, index=0):
    return page.evaluate(
        "i => document.querySelectorAll('samsung-food-meal-plan-card')[i].shadowRoot.querySelector('ha-card').innerText", index
    )


def test_week_layout(page):
    render(page, THEMES["light"], [[{"layout": "week", "title": "Meal plan"}]])
    root = "document.querySelector('samsung-food-meal-plan-card').shadowRoot"
    assert page.evaluate(f"{root}.querySelectorAll('.day').length") == 7
    assert page.evaluate(f"{root}.querySelector('.day.today .dom').textContent") == "15"
    # Today's two dinners both show, after lunch.
    names = page.evaluate(f"[...{root}.querySelectorAll('.day.today .name')].map(e => e.textContent)")
    assert names == ["Lemony White Bean Soup", "Sheet Pan Gnocchi with Sausage", "Garlicky Green Beans"]
    links = page.evaluate(f"[...{root}.querySelectorAll('a.tile')].map(a => [a.target, a.href])")
    assert all(t == "_blank" for t, _ in links)
    assert links[0][1] == "https://kingarthurbaking.com/classic-sourdough-pancakes-or-waffles"
    assert page.evaluate(f"{root}.querySelectorAll('.day.past').length") == 4
    assert page.evaluate(f"{root}.querySelectorAll('.img.placeholder').length") == 1
    # Nothing overflows the card horizontally.
    assert page.evaluate(f"{root}.querySelector('ha-card').scrollWidth <= {root}.querySelector('ha-card').clientWidth")


def test_agenda_hides_past_days(page):
    render(page, THEMES["light"], [[{"layout": "agenda", "show_past_days": False}]])
    text = card_text(page)
    assert text.startswith("15\nToday")
    assert "Pancakes" not in text and "Apple Crumble Bars" in text


def test_next_layout(page):
    render(page, THEMES["light"], [[{"layout": "next"}]])  # 12:30 -> lunch
    assert card_text(page).startswith("NEXT UP · LUNCH\nLemony White Bean Soup")


def test_html_is_escaped(page):
    evil = [dict(SAMPLE_WEEK[3], name="<img src=x onerror=alert(1)>")]
    page.evaluate(
        """meals => { const c = document.querySelector('samsung-food-meal-plan-card');
            c.hass = { states: { 'sensor.samsung_food_meal_plan': { state: 'y', last_updated: 'later', attributes: { meals } } } }; }""",
        evil,
    )
    assert page.evaluate("document.querySelector('samsung-food-meal-plan-card').shadowRoot.querySelectorAll('img[src=x]').length") == 0


def test_missing_entity(page):
    render(page, THEMES["light"], [[{"layout": "week", "entity": "sensor.nope"}]])
    assert "Entity not found: sensor.nope" in card_text(page)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        pg.clock.set_fixed_time(datetime.fromisoformat(f"{TODAY}T12:30:00"))
        pg.set_content(HARNESS)
        pg.add_script_tag(content=CARD_JS, type="module")
        pg.wait_for_function("!!customElements.get('samsung-food-meal-plan-card')")
        for name, theme in THEMES.items():
            render(pg, theme, [
                [{"layout": "next", "title": ""}, {"layout": "next", "breakfast_until": "11:00", "lunch_until": "12:00"}],
                [{"layout": "week", "title": "This week"}],
                [{"layout": "agenda", "title": "Agenda"}],
            ])
            pg.screenshot(path=str(out / f"card-{name}.png"), full_page=True)
        browser.close()
    print(json.dumps([str(out / f"card-{n}.png") for n in THEMES]))
