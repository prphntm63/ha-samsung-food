"""Samsung Food shopping lists as Home Assistant to-do lists."""

from __future__ import annotations

from dataclasses import replace

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ListContents, SamsungFoodError
from .coordinator import SamsungFoodConfigEntry, SamsungFoodCoordinator
from .entity import SamsungFoodEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungFoodConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_lists() -> None:
        new = [lid for lid in coordinator.data.lists if lid not in known]
        known.update(new)
        if new:
            async_add_entities(SamsungFoodShoppingList(coordinator, lid) for lid in new)

    add_new_lists()
    entry.async_on_unload(coordinator.async_add_listener(add_new_lists))


class SamsungFoodShoppingList(SamsungFoodEntity, TodoListEntity):
    _attr_icon = "mdi:cart"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(self, coordinator: SamsungFoodCoordinator, list_id: str) -> None:
        super().__init__(coordinator, f"list_{list_id}")
        self._list_id = list_id

    @property
    def _contents(self) -> ListContents | None:
        return self.coordinator.data.lists.get(self._list_id)

    @property
    def available(self) -> bool:
        return super().available and self._contents is not None

    @property
    def name(self) -> str:
        contents = self._contents
        return contents.list.name if contents and contents.list.name else "Shopping list"

    @property
    def todo_items(self) -> list[TodoItem] | None:
        contents = self._contents
        if contents is None:
            return None
        items = sorted(contents.items, key=lambda i: i.checked)  # stable: keeps app order
        return [
            TodoItem(
                uid=item.id,
                summary=item.name,
                status=TodoItemStatus.COMPLETED if item.checked else TodoItemStatus.NEEDS_ACTION,
                description=" · ".join(p for p in (item.amount, item.comment) if p) or None,
            )
            for item in items
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self._run(
            self.coordinator.client.add_item(
                self._list_id,
                item.summary or "",
                checked=item.status == TodoItemStatus.COMPLETED,
            )
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        contents = self._contents
        existing = next((i for i in contents.items if i.id == item.uid), None) if contents else None
        if existing is None:
            raise HomeAssistantError(f"Item {item.uid} is no longer on the list")
        updated = replace(
            existing,
            name=item.summary or existing.name,
            checked=item.status == TodoItemStatus.COMPLETED,
        )
        await self._run(self.coordinator.client.update_item(self._list_id, updated))

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        await self._run(self.coordinator.client.delete_items(self._list_id, uids))

    async def _run(self, coro) -> None:
        try:
            await coro
        except SamsungFoodError as err:
            raise HomeAssistantError(f"Samsung Food: {err}") from err
        await self.coordinator.async_refresh()
