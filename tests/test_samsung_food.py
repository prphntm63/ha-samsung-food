"""Tests for the samsung_food custom integration against a fake gRPC-Web server."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_food import api as sf_api
from custom_components.samsung_food.const import DOMAIN
from custom_components.samsung_food.coordinator import week_start_for
from custom_components.samsung_food.proto import (
    Msg,
    enc_bool,
    enc_float,
    enc_int,
    enc_msg,
    enc_str,
    grpc_web_frame,
    parse_grpc_web,
)

LIST_ID = "list1"


def _today() -> date:
    return dt_util.now().date()


def _date(d: date) -> bytes:
    return enc_int(1, d.year) + enc_int(2, d.month) + enc_int(3, d.day)


class FakeSamsungFood:
    """Just enough of the Samsung Food API to exercise the integration."""

    def __init__(self) -> None:
        self.valid_tokens = {"tok-1"}
        self.next_token = 1
        self.logins = 0
        self.items = [
            {"id": "i1", "name": "Milk", "checked": False, "qty": 2.0, "unit": "l"},
            {"id": "i2", "name": "Eggs", "checked": True},
        ]
        self.sync = 100
        self.sync_requests: list[Msg] = []
        self.last_sync_seen: int | None = None
        self.recipe_lookups: list[str] = []

    def reply(self, msg: bytes = b"", status: int = 0, message: str = "") -> web.Response:
        trailer = f"grpc-status:{status}\r\ngrpc-message:{message}\r\n".encode()
        body = (grpc_web_frame(msg) if not status else b"") + b"\x80" + len(trailer).to_bytes(4, "big") + trailer
        return web.Response(body=body, content_type="application/grpc-web+proto")

    async def handle(self, request: web.Request) -> web.Response:
        rpc = request.match_info["rpc"]
        msg_bytes, _ = parse_grpc_web(await request.read())
        req = Msg(msg_bytes or b"")
        if rpc.endswith("AuthAPI/Login"):
            assert request.headers.get("X-Whisk-Client-Id") == sf_api.CLIENT_ID
            if req.str(1) != "me@example.com" or req.str(2) != "hunter2":
                return self.reply(status=3, message='{"code":"ERROR_AUTH_WRONG_PASSWORD"}')
            self.logins += 1
            self.next_token += 1
            tok = f"tok-{self.next_token}"
            self.valid_tokens = {tok}
            token = enc_str(1, tok) + enc_str(2, "Bearer") + enc_int(3, 3600 * 24 * 30)
            return self.reply(enc_msg(1, enc_msg(1, token) + enc_msg(2, enc_str(1, "user-42"))))

        auth = request.headers.get("Authorization", "")
        if auth.removeprefix("Bearer ") not in self.valid_tokens:
            return self.reply(status=16, message='{"code":"ERROR_AUTH_TOKEN_NOT_FOUND"}')

        if rpc.endswith("ListAPI/GetLists"):
            lst = enc_str(1, LIST_ID) + enc_str(2, "Groceries") + enc_bool(3, True)
            return self.reply(enc_msg(1, enc_msg(1, lst) + enc_int(3, len(self.items))))
        if rpc.endswith("ListAPI/GetList"):
            assert req.str(1) == LIST_ID
            items = b""
            for it in self.items:
                body = enc_str(1, it["name"]) + enc_float(4, it.get("qty")) + enc_str(5, it.get("unit"))
                items += enc_msg(1, enc_str(1, it["id"]) + enc_msg(2, body) + enc_bool(3, it["checked"]))
            return self.reply(enc_int(1, self.sync) + enc_msg(2, enc_str(1, LIST_ID)) + enc_msg(4, items))
        if rpc.endswith("ListAPI/SyncItems"):
            self.sync_requests.append(req)
            self.last_sync_seen = req.int(2)
            for op in req.msgs(3):
                if op.has(1):
                    c = op.msg(1)
                    self.items.append({"id": f"new{len(self.items)}", "name": c.msg(1).str(1), "checked": c.bool(5)})
                elif op.has(2):
                    u = op.msg(2)
                    for it in self.items:
                        if it["id"] == u.str(1):
                            it.update(name=u.msg(2).str(1), checked=u.bool(3), qty=u.msg(2).float(4), unit=u.msg(2).str(5))
                elif op.has(3):
                    self.items = [it for it in self.items if it["id"] != op.msg(3).str(1)]
            self.sync += 1
            return self.reply(enc_int(1, self.sync))
        if rpc.endswith("RecipeAPI/GetRecipe"):
            self.recipe_lookups.append(req.str(1))
            if req.str(1) == "r2":  # a recipe with no original source
                return self.reply(enc_msg(1, enc_str(1, "r2")))
            src = enc_str(1, "example.com") + enc_str(2, "example.com") + enc_str(
                3, f"https://example.com/{req.str(1)}?a=1&utm_source=whisk&utm_medium=webapp"
            )
            return self.reply(enc_msg(1, enc_str(1, req.str(1)) + enc_msg(8, src)))
        if rpc.endswith("MealPlanV2API/GetMealSchedule"):
            period = req.msg(2)
            start, end = period.msg(1), period.msg(2)
            start_d = date(start.int(1), start.int(2), start.int(3))
            end_d = date(end.int(1), end.int(2), end.int(3))
            assert "recipe.name" in req.msg(3).strs(1)
            meals = b""
            for i, (offset, mt, name) in enumerate([(0, 3, "Tacos"), (0, 1, "Pancakes"), (2, 3, "Salmon")]):
                d = _today() + timedelta(days=offset)
                if not start_d <= d <= end_d:
                    continue
                recipe = enc_str(1, f"r{i}") + enc_str(2, name) + enc_msg(6, enc_msg(1, enc_str(1, f"https://img/{i}.jpg")))
                sched = enc_msg(1, enc_msg(1, _date(d)) + enc_int(2, mt))
                meals += enc_msg(1, enc_str(1, f"m{i}") + enc_msg(2, sched) + enc_msg(4, enc_msg(1, recipe) + enc_str(3, f"c{i}")))
            return self.reply(meals)
        return self.reply(status=12, message="unimplemented " + rpc)


@pytest.fixture
async def fake_server(hass: HomeAssistant, enable_custom_integrations, socket_enabled):
    fake = FakeSamsungFood()
    app = web.Application()
    app.router.add_post("/api/grpc-web/{rpc:.+}", fake.handle)
    server = TestServer(app)
    await server.start_server()
    with patch.object(sf_api, "BASE_URL", str(server.make_url("/api/grpc-web/"))):
        yield fake
    await server.close()


async def _setup(hass: HomeAssistant, fake: FakeSamsungFood) -> MockConfigEntry:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "wrong"}
    )
    assert bad["errors"] == {"base": "invalid_auth"}
    done = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "hunter2"}
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "user-42"
    return entry


async def test_full_flow(hass: HomeAssistant, fake_server: FakeSamsungFood) -> None:
    fake = fake_server
    TODAY = _today()
    entry = await _setup(hass, fake)
    logins_after_setup = fake.logins
    # Setup reuses the token from the config flow instead of logging in again.
    assert logins_after_setup == 1

    # --- todo entity
    state = hass.states.get("todo.samsung_food_groceries")
    assert state is not None, [s.entity_id for s in hass.states.async_all()]
    assert state.state == "1"  # one unchecked item

    items = await hass.services.async_call(
        "todo", "get_items", {}, target={"entity_id": state.entity_id}, blocking=True, return_response=True
    )
    got = items[state.entity_id]["items"]
    assert [(i["summary"], i["status"]) for i in got] == [("Milk", "needs_action"), ("Eggs", "completed")]
    assert got[0]["description"] == "2 l"

    await hass.services.async_call("todo", "add_item", {"item": "Pears"}, target={"entity_id": state.entity_id}, blocking=True)
    assert fake.items[-1] == {"id": "new2", "name": "Pears", "checked": False}
    assert fake.last_sync_seen == 100  # used the latest sync marker
    assert hass.states.get(state.entity_id).state == "2"

    # Checking off Milk must keep its quantity/unit.
    await hass.services.async_call(
        "todo", "update_item", {"item": "Milk", "status": "completed"}, target={"entity_id": state.entity_id}, blocking=True
    )
    milk = next(i for i in fake.items if i["id"] == "i1")
    assert milk["checked"] is True and milk["qty"] == 2.0 and milk["unit"] == "l" and milk["name"] == "Milk"

    await hass.services.async_call("todo", "remove_item", {"item": ["Eggs"]}, target={"entity_id": state.entity_id}, blocking=True)
    assert [i["id"] for i in fake.items] == ["i1", "new2"]

    # --- meal plan sensor
    sensor = hass.states.get("sensor.samsung_food_meal_plan")
    assert sensor.state == "Breakfast: Pancakes, Dinner: Tacos"
    meals = sensor.attributes["meals"] + sensor.attributes["next_week_meals"]
    assert [m["name"] for m in meals] == ["Pancakes", "Tacos", "Salmon"]
    assert meals[0]["url"] == "https://app.samsungfood.com/recipes/r1"
    assert meals[0]["image"] == "https://img/1.jpg"
    assert meals[0]["source"] == "example.com"
    assert meals[0]["source_url"] == "https://example.com/r1?a=1"  # utm_* stripped
    assert meals[0]["link"] == "https://example.com/r1?a=1"
    assert meals[2]["link"] == "https://app.samsungfood.com/recipes/r2"  # no source -> Samsung Food page
    # Each recipe's source is looked up once, then cached across polls.
    lookups = len(fake.recipe_lookups)
    assert sorted(fake.recipe_lookups) == ["r0", "r1", "r2"]
    assert sensor.attributes["week_start"] == week_start_for(TODAY).isoformat()

    # --- calendar
    cal = hass.states.get("calendar.samsung_food_meal_plan")
    assert cal.attributes["message"] == "Breakfast: Pancakes"
    start = dt_util.start_of_local_day()
    events = await hass.services.async_call(
        "calendar", "get_events", {"start_date_time": start, "end_date_time": start + timedelta(days=7)},
        target={"entity_id": cal.entity_id}, blocking=True, return_response=True,
    )
    evs = events[cal.entity_id]["events"]
    assert [e["summary"] for e in evs] == ["Breakfast: Pancakes", "Dinner: Tacos", "Dinner: Salmon"]
    assert evs[0]["start"] == TODAY.isoformat()
    assert evs[0]["description"] == "https://example.com/r1?a=1\nhttps://app.samsungfood.com/recipes/r1"
    await entry.runtime_data.async_refresh()
    assert len(fake.recipe_lookups) == lookups

    # --- expired token: the next call logs in again transparently and saves the new token
    fake.valid_tokens = set()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.last_update_success
    assert fake.logins == logins_after_setup + 1
    assert entry.data["access_token"] in fake.valid_tokens

    # --- password changed: integration asks for reauth
    fake.valid_tokens = set()
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PASSWORD: "old"})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)
    reauth = next(f for f in flows if f["context"]["source"] == "reauth")
    res = await hass.config_entries.flow.async_configure(reauth["flow_id"], {CONF_PASSWORD: "hunter2"})
    assert res["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert hass.states.get("todo.samsung_food_groceries").state == "1"


def test_proto_roundtrip() -> None:
    body = enc_str(1, "héllo") + enc_int(2, 1792296726182) + enc_float(3, 1.5) + enc_msg(4, enc_str(1, "x"))
    m = Msg(body)
    assert m.str(1) == "héllo" and m.int(2) == 1792296726182 and m.float(3) == 1.5 and m.msg(4).str(1) == "x"
    assert m.msg(9).str(1) == "" and m.msgs(9) == []
    framed = grpc_web_frame(body) + b"\x80\x00\x00\x00\x0fgrpc-status:0\r\n"
    msg, trailers = parse_grpc_web(framed)
    assert msg == body and trailers == {"grpc-status": "0"}


def test_parses_real_sync_response() -> None:
    """Bytes captured from the live SyncItems call that added 'Pears'."""
    item = enc_str(1, "0123456789abcdef0123456789abcdef") + enc_msg(2, enc_str(1, "pears"))
    resp = enc_int(1, 1792296726182) + enc_msg(2, enc_msg(1, enc_msg(1, item)))
    m = Msg(resp)
    assert m.int(1) == 1792296726182
    assert m.msg(2).msg(1).msg(1).msg(2).str(1) == "pears"


async def test_card_is_served_and_registered(
    hass: HomeAssistant, enable_custom_integrations, hass_client
) -> None:
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
    from homeassistant.setup import async_setup_component

    from custom_components.samsung_food.const import CARD_URL

    # The real frontend needs the hass_frontend package; fake just the URL registry.
    hass.config.components.add("frontend")
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *args: None, [])
    assert await async_setup_component(hass, DOMAIN, {})
    assert any(url.startswith(f"{CARD_URL}?v=") for url in hass.data[DATA_EXTRA_MODULE_URL].urls)

    client = await hass_client()
    resp = await client.get(CARD_URL)
    assert resp.status == 200
    assert "customElements.define(CARD_TAG" in await resp.text()
