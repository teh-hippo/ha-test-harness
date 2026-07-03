"""Synthetic climate entities for HomeKit HeaterCooler validation.

Each entity implements the full climate write path so an aiohomekit client
or the iOS Home app can drive it end to end. Values are fully synthetic;
initial state is seeded from the owner's saved read-only history.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


class MockClimate(ClimateEntity):
    """A synthetic climate entity driven entirely by its supported features."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_should_poll = False

    def __init__(
        self,
        *,
        object_id: str,
        name: str,
        supported_features: ClimateEntityFeature,
        hvac_modes: list[HVACMode],
        fan_modes: list[str],
        min_temp: float,
        max_temp: float,
        target_temp_step: float,
        hvac_mode: HVACMode,
        current_temperature: float,
        fan_mode: str,
        hvac_action: HVACAction,
        swing_modes: list[str] | None = None,
        swing_mode: str | None = None,
        target_temperature: float | None = None,
        target_temperature_low: float | None = None,
        target_temperature_high: float | None = None,
        hvac_action_map: dict[HVACMode, HVACAction] | None = None,
    ) -> None:
        """Initialise the seeded state."""
        self.entity_id = f"climate.{object_id}"
        self._attr_unique_id = object_id
        self._attr_name = name
        self._attr_supported_features = supported_features
        self._attr_hvac_modes = hvac_modes
        self._attr_fan_modes = fan_modes
        self._attr_swing_modes = swing_modes
        self._attr_min_temp = min_temp
        self._attr_max_temp = max_temp
        self._attr_target_temperature_step = target_temp_step
        self._attr_hvac_mode = hvac_mode
        self._attr_current_temperature = current_temperature
        self._attr_fan_mode = fan_mode
        self._attr_swing_mode = swing_mode
        self._attr_hvac_action = hvac_action
        self._attr_target_temperature = target_temperature
        self._attr_target_temperature_low = target_temperature_low
        self._attr_target_temperature_high = target_temperature_high
        self._hvac_action_map = hvac_action_map
        self._last_on_hvac_mode = hvac_mode if hvac_mode != HVACMode.OFF else self._first_on_mode()

    def _first_on_mode(self) -> HVACMode:
        """Return the first non-off mode to use when turning on."""
        for mode in self._attr_hvac_modes:
            if mode != HVACMode.OFF:
                return mode
        return HVACMode.OFF

    @property
    def _is_dual(self) -> bool:
        """Return True when this entity uses a low/high target range."""
        return bool(self._attr_supported_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)

    def _recompute_action(self) -> None:
        """Derive a sensible hvac_action from the current state."""
        mode = self._attr_hvac_mode
        if self._hvac_action_map is not None:
            # Mirror HA's daikin integration: action derives from mode alone,
            # so dry/fan_only/heat_cool report no action at all.
            self._attr_hvac_action = self._hvac_action_map.get(mode)
            return
        current = self._attr_current_temperature
        if mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif mode == HVACMode.HEAT:
            self._attr_hvac_action = HVACAction.HEATING if current < self._attr_target_temperature else HVACAction.IDLE
        elif mode == HVACMode.COOL:
            self._attr_hvac_action = HVACAction.COOLING if current > self._attr_target_temperature else HVACAction.IDLE
        elif mode == HVACMode.HEAT_COOL and self._is_dual:
            if current < self._attr_target_temperature_low:
                self._attr_hvac_action = HVACAction.HEATING
            elif current > self._attr_target_temperature_high:
                self._attr_hvac_action = HVACAction.COOLING
            else:
                self._attr_hvac_action = HVACAction.IDLE
        elif mode in (HVACMode.HEAT_COOL, HVACMode.AUTO):
            if current < self._attr_target_temperature:
                self._attr_hvac_action = HVACAction.HEATING
            elif current > self._attr_target_temperature:
                self._attr_hvac_action = HVACAction.COOLING
            else:
                self._attr_hvac_action = HVACAction.IDLE
        else:
            self._attr_hvac_action = HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new HVAC mode."""
        self._attr_hvac_mode = hvac_mode
        if hvac_mode != HVACMode.OFF:
            self._last_on_hvac_mode = hvac_mode
        self._recompute_action()
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature(s)."""
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            self._attr_hvac_mode = hvac_mode
            if hvac_mode != HVACMode.OFF:
                self._last_on_hvac_mode = hvac_mode
        if self._is_dual:
            if (low := kwargs.get(ATTR_TARGET_TEMP_LOW)) is not None:
                self._attr_target_temperature_low = low
            if (high := kwargs.get(ATTR_TARGET_TEMP_HIGH)) is not None:
                self._attr_target_temperature_high = high
        elif (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = temperature
        self._recompute_action()
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set a new fan mode."""
        self._attr_fan_mode = fan_mode
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set a new swing mode."""
        self._attr_swing_mode = swing_mode
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn the entity on by restoring the last active mode."""
        self._attr_hvac_mode = self._last_on_hvac_mode
        self._recompute_action()
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        if self._attr_hvac_mode != HVACMode.OFF:
            self._last_on_hvac_mode = self._attr_hvac_mode
        self._attr_hvac_mode = HVACMode.OFF
        self._recompute_action()
        self.async_write_ha_state()


def _build_entities() -> list[MockClimate]:
    """Build the three synthetic climate entities."""
    return [
        MockClimate(
            object_id="mock_daikin",
            name="Mock Daikin",
            supported_features=(
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.FAN_MODE
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
            ),
            hvac_modes=[
                HVACMode.FAN_ONLY,
                HVACMode.DRY,
                HVACMode.COOL,
                HVACMode.HEAT,
                HVACMode.HEAT_COOL,
                HVACMode.OFF,
            ],
            fan_modes=["Auto", "Low", "Mid", "High", "Low/Auto", "Mid/Auto", "High/Auto"],
            min_temp=7,
            max_temp=35,
            target_temp_step=1,
            hvac_mode=HVACMode.HEAT,
            target_temperature=20,
            current_temperature=19,
            fan_mode="High",
            hvac_action=HVACAction.HEATING,
            hvac_action_map={
                HVACMode.COOL: HVACAction.COOLING,
                HVACMode.HEAT: HVACAction.HEATING,
                HVACMode.OFF: HVACAction.OFF,
            },
        ),
        MockClimate(
            object_id="mock_dual_swing",
            name="Mock Dual Swing",
            supported_features=(
                ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
                | ClimateEntityFeature.FAN_MODE
                | ClimateEntityFeature.SWING_MODE
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
            ),
            hvac_modes=[HVACMode.OFF, HVACMode.COOL, HVACMode.HEAT, HVACMode.HEAT_COOL],
            fan_modes=["auto", "low", "medium", "high"],
            swing_modes=["off", "vertical", "horizontal", "both"],
            min_temp=16,
            max_temp=32,
            target_temp_step=0.5,
            hvac_mode=HVACMode.HEAT_COOL,
            target_temperature_low=20,
            target_temperature_high=24,
            current_temperature=22,
            fan_mode="medium",
            swing_mode="vertical",
            hvac_action=HVACAction.COOLING,
        ),
        MockClimate(
            object_id="mock_heat_cool_auto",
            name="Mock Heat Cool Auto",
            supported_features=(
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.FAN_MODE
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
            ),
            hvac_modes=[
                HVACMode.OFF,
                HVACMode.HEAT,
                HVACMode.COOL,
                HVACMode.HEAT_COOL,
                HVACMode.AUTO,
            ],
            fan_modes=["auto", "low", "high"],
            min_temp=16,
            max_temp=30,
            target_temp_step=0.5,
            hvac_mode=HVACMode.AUTO,
            target_temperature=23,
            current_temperature=21,
            fan_mode="auto",
            hvac_action=HVACAction.IDLE,
        ),
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the mock climate entities from a config entry."""
    async_add_entities(_build_entities())
