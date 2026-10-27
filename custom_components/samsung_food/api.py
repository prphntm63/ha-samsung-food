"""Async client for the (unofficial) Samsung Food gRPC-Web API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from .proto import (
    Msg,
    enc_bool,
    enc_float,
    enc_int,
    enc_msg,
    enc_str,
    enc_strs,
    grpc_web_frame,
    parse_grpc_web,
)

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://app.samsungfood.com/api/grpc-web/"
RECIPE_URL = "https://app.samsungfood.com/recipes/{}"
# Public client ID baked into the production web app; required for unauthenticated calls (login).
CLIENT_ID = "6FLMPrxutqIWIiua5sVDmqpTyOLGle7Y2pbccmKfmwFcMdNnXZUW03ftI6mUDUHT"

GRPC_UNAUTHENTICATED = 16
# Re-login this long before the token's stated expiry.
TOKEN_EXPIRY_MARGIN = 24 * 3600

MEAL_TIMES = {1: "Breakfast", 2: "Lunch", 3: "Dinner", 4: "Snack"}


class SamsungFoodError(Exception):
    """Base error."""


class SamsungFoodAuthError(SamsungFoodError):
    """Credentials were rejected."""


class SamsungFoodApiError(SamsungFoodError):
    """Any other API failure."""

    def __init__(self, rpc: str, status: int, message: str) -> None:
        super().__init__(f"{rpc} failed with gRPC status {status}: {message}")
        self.status = status
        self.code = _error_code(message)


def _error_code(message: str) -> str:
    try:
        return json.loads(message).get("code", "")
    except (ValueError, AttributeError):
        return ""


@dataclass
class ShoppingList:
    id: str
    name: str
    primary: bool


@dataclass
class ListItem:
    id: str
    name: str
    checked: bool
    brand: str = ""
    comment: str = ""
    quantity: float | None = None
    unit: str = ""
    created: int = 0

    @property
    def amount(self) -> str:
        """Human-readable quantity, e.g. '2 lb'."""
        if not self.quantity:
            return self.unit
        qty = f"{self.quantity:g}"
        return f"{qty} {self.unit}".strip()


@dataclass
class ListContents:
    list: ShoppingList
    sync: int
    items: list[ListItem] = field(default_factory=list)


@dataclass
class Meal:
    id: str
    day: date | None
    meal_time: str
    name: str
    recipe_id: str = ""
    image_url: str = ""
    source_name: str = ""
    source_url: str = ""

    @property
    def url(self) -> str:
        """Samsung Food recipe page (falls back to the original site)."""
        if self.recipe_id:
            return RECIPE_URL.format(self.recipe_id)
        return self.source_url

    @property
    def link(self) -> str:
        """Original recipe site if known, else the Samsung Food page."""
        return self.source_url or self.url


@dataclass
class RecipeSource:
    name: str = ""
    url: str = ""
    image_url: str = ""


def _strip_tracking(url: str) -> str:
    """Drop the utm_* parameters Samsung Food appends to source URLs."""
    if not url:
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


@dataclass
class Account:
    user_id: str
    access_token: str
    expires_at: float


def _enc_date(d: date) -> bytes:
    return enc_int(1, d.year) + enc_int(2, d.month) + enc_int(3, d.day)


def _dec_date(m: Msg) -> date | None:
    if not m.has(1):
        return None
    try:
        return date(m.int(1), m.int(2), m.int(3))
    except ValueError:
        return None


def _dec_item(m: Msg) -> ListItem:
    item = m.msg(2)
    return ListItem(
        id=m.str(1),
        name=item.str(1),
        brand=item.str(2),
        comment=item.str(3),
        quantity=item.float(4),
        unit=item.str(5),
        checked=m.bool(3),
        created=m.int(10),
    )


def _enc_item_body(item: ListItem) -> bytes:
    return (
        enc_str(1, item.name)
        + enc_str(2, item.brand)
        + enc_str(3, item.comment)
        + enc_float(4, item.quantity)
        + enc_str(5, item.unit)
    )


class SamsungFoodClient:
    """Talks to Samsung Food, logging in again whenever the token is rejected."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        access_token: str | None = None,
        expires_at: float = 0,
        on_token: Callable[[Account], Awaitable[None] | None] | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token = access_token
        self._expires_at = expires_at
        self._on_token = on_token
        self._login_lock = asyncio.Lock()
        # Recipe sources don't change, so look each one up only once.
        self._recipe_sources: dict[str, RecipeSource] = {}

    # ------------------------------------------------------------ transport

    async def _raw_call(self, rpc: str, body: bytes, token: str | None) -> Msg:
        headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "X-Whisk-App-Name": "webapp",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["X-Whisk-Client-Id"] = CLIENT_ID
        try:
            async with self._session.post(
                BASE_URL + rpc,
                data=grpc_web_frame(body),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.read()
                if resp.status != 200:
                    raise SamsungFoodApiError(rpc, -resp.status, f"HTTP {resp.status}")
                hdr_status = resp.headers.get("grpc-status")
                hdr_message = resp.headers.get("grpc-message", "")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SamsungFoodError(f"{rpc}: {err}") from err

        message, trailers = parse_grpc_web(raw)
        status = int(trailers.get("grpc-status", hdr_status or 0))
        if status:
            raise SamsungFoodApiError(
                rpc, status, trailers.get("grpc-message", hdr_message)
            )
        return Msg(message or b"")

    async def _call(self, rpc: str, body: bytes = b"") -> Msg:
        if not self._token or time.time() > self._expires_at - TOKEN_EXPIRY_MARGIN:
            await self.login()
        token = self._token
        try:
            return await self._raw_call(rpc, body, token)
        except SamsungFoodApiError as err:
            if err.status != GRPC_UNAUTHENTICATED:
                raise
            _LOGGER.debug("Token rejected by %s (%s); logging in again", rpc, err.code)
        await self.login(stale_token=token)
        return await self._raw_call(rpc, body, self._token)

    # ------------------------------------------------------------ auth

    async def login(self, stale_token: str | None = None) -> Account:
        """Log in with email/password and store the new access token."""
        async with self._login_lock:
            # Another caller may have already refreshed while we waited.
            if stale_token is not None and self._token and self._token != stale_token:
                return Account("", self._token, self._expires_at)
            try:
                resp = await self._raw_call(
                    "whisk.x.user.v1.AuthAPI/Login",
                    enc_str(1, self._email) + enc_str(2, self._password),
                    None,
                )
            except SamsungFoodApiError as err:
                if err.code.startswith(("ERROR_AUTH", "ERROR_INVALID")) or err.status in (
                    GRPC_UNAUTHENTICATED,
                    7,  # PERMISSION_DENIED
                ):
                    raise SamsungFoodAuthError(err.code or str(err)) from err
                raise
            auth = resp.msg(1)
            token = auth.msg(1)
            access = token.str(1)
            if not access:
                raise SamsungFoodAuthError("Login response contained no access token")
            expires_in = token.int(3) or 30 * 24 * 3600
            self._token = access
            self._expires_at = time.time() + expires_in
            account = Account(auth.msg(2).str(1), access, self._expires_at)
            _LOGGER.debug("Logged in to Samsung Food; token valid for %ss", expires_in)
            if self._on_token:
                result = self._on_token(account)
                if asyncio.iscoroutine(result):
                    await result
            return account

    async def get_user_id(self) -> str:
        resp = await self._call("whisk.x.user.v1.UserAPI/GetMe")
        return resp.msg(1).str(1)

    # ------------------------------------------------------------ shopping lists

    async def get_lists(self) -> list[ShoppingList]:
        resp = await self._call("whisk.x.list.v1.ListAPI/GetLists")
        out = []
        for entry in resp.msgs(1):
            lst = entry.msg(1)
            out.append(ShoppingList(lst.str(1), lst.str(2), lst.bool(3)))
        return out

    async def get_list(self, list_id: str) -> ListContents:
        resp = await self._call("whisk.x.list.v1.ListAPI/GetList", enc_str(1, list_id))
        lst = resp.msg(2)
        return ListContents(
            list=ShoppingList(lst.str(1) or list_id, lst.str(2), lst.bool(3)),
            sync=resp.int(1),
            items=[_dec_item(m) for m in resp.msg(4).msgs(1)],
        )

    async def _sync_items(self, list_id: str, ops: list[bytes]) -> None:
        # Fetch the current sync marker right before writing so the server
        # treats our ops as being applied to the latest version of the list.
        sync = (await self._call("whisk.x.list.v1.ListAPI/GetList", enc_str(1, list_id))).int(1)
        body = enc_str(1, list_id) + enc_int(2, sync) + b"".join(enc_msg(3, op) for op in ops)
        await self._call("whisk.x.list.v1.ListAPI/SyncItems", body)

    async def add_item(self, list_id: str, text: str, checked: bool = False) -> None:
        """Add free text; the server parses quantity/unit and categorises it."""
        create = enc_msg(1, enc_str(1, text)) + enc_bool(5, checked) + enc_bool(6, True)
        await self._sync_items(list_id, [enc_msg(1, create)])

    async def update_item(self, list_id: str, item: ListItem) -> None:
        """Replace an item's name/details and checked state."""
        update = enc_str(1, item.id) + enc_msg(2, _enc_item_body(item)) + enc_bool(3, item.checked)
        await self._sync_items(list_id, [enc_msg(2, update)])

    async def delete_items(self, list_id: str, item_ids: list[str]) -> None:
        if item_ids:
            await self._sync_items(
                list_id, [enc_msg(3, enc_str(1, item_id)) for item_id in item_ids]
            )

    # ------------------------------------------------------------ meal plan

    async def get_meals(self, start: date, end: date) -> list[Meal]:
        """Scheduled meals from start to end (inclusive)."""
        mask = enc_strs(
            1,
            ["recipe.name", "recipe.images", "recipe.source", "food.title", "food.image_url"],
        )
        body = enc_msg(2, enc_msg(1, _enc_date(start)) + enc_msg(2, _enc_date(end))) + enc_msg(
            3, mask
        )
        resp = await self._call("whisk.x.mealplan.v2.MealPlanV2API/GetMealSchedule", body)
        meals = []
        for m in resp.msgs(1):
            scheduled = m.msg(2).msg(1)
            if not scheduled.has(1):
                continue  # unscheduled ("queue") meals have no date
            day = _dec_date(scheduled.msg(1))
            meal_time = MEAL_TIMES.get(scheduled.int(2), "Meal")
            for content in m.msgs(4) or [Msg()]:
                recipe = content.msg(1)
                food = content.msg(2)
                if recipe.has(1):
                    images = recipe.msgs(6)
                    source = recipe.msg(8)
                    meals.append(
                        Meal(
                            id=f"{m.str(1)}-{content.str(3) or recipe.str(1)}",
                            day=day,
                            meal_time=meal_time,
                            name=recipe.str(2) or "Recipe",
                            recipe_id=recipe.str(1),
                            image_url=images[0].msg(1).str(1) if images else "",
                            source_name=source.str(2) or source.str(1),
                            source_url=source.str(3),
                        )
                    )
                elif food.has(1):
                    meals.append(
                        Meal(
                            id=f"{m.str(1)}-{content.str(3) or food.str(1)}",
                            day=day,
                            meal_time=meal_time,
                            name=food.str(2) or "Food",
                            image_url=food.str(4),
                        )
                    )
        await self._add_recipe_sources(meals)
        order = {name: num for num, name in MEAL_TIMES.items()}
        meals.sort(key=lambda x: (x.day or date.min, order.get(x.meal_time, 9)))
        return meals

    async def get_recipe_source(self, recipe_id: str) -> RecipeSource:
        resp = await self._call(
            "whisk.x.recipe.v1.RecipeAPI/GetRecipe",
            enc_str(1, recipe_id) + enc_msg(2, enc_strs(1, ["recipe.source", "recipe.images"])),
        )
        recipe = resp.msg(1)
        source = recipe.msg(8)
        images = recipe.msgs(6)
        return RecipeSource(
            name=source.str(2) or source.str(1),
            url=_strip_tracking(source.str(3)),
            image_url=images[0].msg(1).str(1) if images else "",
        )

    async def _add_recipe_sources(self, meals: list[Meal]) -> None:
        """GetMealSchedule never includes the source site, so fetch it per recipe."""
        missing = {m.recipe_id for m in meals if m.recipe_id} - self._recipe_sources.keys()
        if missing:
            ids = sorted(missing)
            results = await asyncio.gather(
                *(self.get_recipe_source(rid) for rid in ids), return_exceptions=True
            )
            for rid, result in zip(ids, results):
                if isinstance(result, RecipeSource):
                    self._recipe_sources[rid] = result
                else:
                    # Don't fail the whole update; retry on the next poll.
                    _LOGGER.debug("Couldn't fetch source for recipe %s: %s", rid, result)
        for meal in meals:
            src = self._recipe_sources.get(meal.recipe_id)
            if src:
                meal.source_name = meal.source_name or src.name
                meal.source_url = meal.source_url or src.url
                meal.image_url = meal.image_url or src.image_url
