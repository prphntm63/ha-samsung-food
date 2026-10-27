"""Constants for the Samsung Food integration."""

from datetime import timedelta

DOMAIN = "samsung_food"

CARD_URL = "/samsung_food/samsung-food-meal-plan.js"

CONF_ACCESS_TOKEN = "access_token"
CONF_EXPIRES_AT = "expires_at"

SCAN_INTERVAL = timedelta(minutes=5)

# Samsung Food's planner weeks start on Sunday (Python weekday 6).
WEEK_START_WEEKDAY = 6
