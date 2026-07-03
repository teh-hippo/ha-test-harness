"""End-to-end write-path tests for the Mock Climate integration."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mock_climate.const import DOMAIN

DAIKIN = "climate.mock_daikin"
DUAL = "climate.mock_dual_swing"
AUTO = "climate.mock_heat_cool_auto"

FEATURES_DAIKIN = 393
FEATURES_DUAL = 426
FEATURES_AUTO = 393


async def _setup_via_config_entry(hass: HomeAssistant) -> None:
    """Set up the integration through a config entry."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, service: str, data: dict) -> None:
    """Call a climate service and wait for completion."""
    await hass.services.async_call(CLIMATE_DOMAIN, service, data, blocking=True)
    await hass.async_block_till_done()


def test_feature_bitmask_arithmetic() -> None:
    """The intended supported_features integers must match the flag maths."""
    assert (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    ) == FEATURES_DAIKIN
    assert (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    ) == FEATURES_DUAL


async def test_entities_created_with_seeded_attributes(hass: HomeAssistant) -> None:
    """All three entities exist with the exact specified attributes."""
    await _setup_via_config_entry(hass)

    # Profile 1 - Mock Daikin.
    daikin = hass.states.get(DAIKIN)
    assert daikin is not None
    assert daikin.state == HVACMode.HEAT
    a = daikin.attributes
    assert a[ATTR_SUPPORTED_FEATURES] == FEATURES_DAIKIN
    assert a[ATTR_HVAC_MODES] == ["fan_only", "dry", "cool", "heat", "heat_cool", "off"]
    assert a[ATTR_FAN_MODES] == [
        "Auto",
        "Low",
        "Mid",
        "High",
        "Low/Auto",
        "Mid/Auto",
        "High/Auto",
    ]
    assert ATTR_SWING_MODES not in a
    assert a[ATTR_MIN_TEMP] == 7
    assert a[ATTR_MAX_TEMP] == 35
    assert a[ATTR_TARGET_TEMP_STEP] == 1
    assert a[ATTR_TEMPERATURE] == 20
    assert a[ATTR_CURRENT_TEMPERATURE] == 19
    assert a[ATTR_FAN_MODE] == "High"
    assert a[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert ATTR_TARGET_TEMP_LOW not in a and ATTR_TARGET_TEMP_HIGH not in a

    # Profile 2 - Mock Dual Swing.
    dual = hass.states.get(DUAL)
    assert dual is not None
    assert dual.state == HVACMode.HEAT_COOL
    a = dual.attributes
    assert a[ATTR_SUPPORTED_FEATURES] == FEATURES_DUAL
    assert a[ATTR_HVAC_MODES] == ["off", "cool", "heat", "heat_cool"]
    assert a[ATTR_FAN_MODES] == ["auto", "low", "medium", "high"]
    assert a[ATTR_SWING_MODES] == ["off", "vertical", "horizontal", "both"]
    assert a[ATTR_MIN_TEMP] == 16
    assert a[ATTR_MAX_TEMP] == 32
    assert a[ATTR_TARGET_TEMP_STEP] == 0.5
    assert a[ATTR_TARGET_TEMP_LOW] == 20
    assert a[ATTR_TARGET_TEMP_HIGH] == 24
    assert ATTR_TEMPERATURE not in a
    assert a[ATTR_CURRENT_TEMPERATURE] == 22
    assert a[ATTR_FAN_MODE] == "medium"
    assert a[ATTR_SWING_MODE] == "vertical"
    assert a[ATTR_HVAC_ACTION] == HVACAction.COOLING

    # Profile 3 - Mock Heat Cool Auto.
    auto = hass.states.get(AUTO)
    assert auto is not None
    assert auto.state == HVACMode.AUTO
    a = auto.attributes
    assert a[ATTR_SUPPORTED_FEATURES] == FEATURES_AUTO
    assert a[ATTR_HVAC_MODES] == ["off", "heat", "cool", "heat_cool", "auto"]
    assert a[ATTR_FAN_MODES] == ["auto", "low", "high"]
    assert ATTR_SWING_MODES not in a
    assert a[ATTR_MIN_TEMP] == 16
    assert a[ATTR_MAX_TEMP] == 30
    assert a[ATTR_TARGET_TEMP_STEP] == 0.5
    assert a[ATTR_TEMPERATURE] == 23
    assert a[ATTR_CURRENT_TEMPERATURE] == 21
    assert a[ATTR_FAN_MODE] == "auto"
    assert a[ATTR_HVAC_ACTION] == HVACAction.IDLE


async def test_daikin_write_path(hass: HomeAssistant) -> None:
    """Single-setpoint writes, fan, turn off/on and daikin action semantics."""
    await _setup_via_config_entry(hass)

    await _call(hass, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: DAIKIN, "hvac_mode": HVACMode.COOL})
    state = hass.states.get(DAIKIN)
    assert state.state == HVACMode.COOL
    # The real daikin derives action from mode alone: cool -> cooling always.
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING

    # The setpoint never changes the action; current_temperature is independent.
    await _call(hass, SERVICE_SET_TEMPERATURE, {ATTR_ENTITY_ID: DAIKIN, ATTR_TEMPERATURE: 25})
    state = hass.states.get(DAIKIN)
    assert state.attributes[ATTR_TEMPERATURE] == 25
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 19

    # fan_only, dry and heat_cool report no action, exactly like the real unit.
    for mode in (HVACMode.FAN_ONLY, HVACMode.DRY, HVACMode.HEAT_COOL):
        await _call(hass, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: DAIKIN, "hvac_mode": mode})
        state = hass.states.get(DAIKIN)
        assert state.state == mode
        assert ATTR_HVAC_ACTION not in state.attributes

    await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_ENTITY_ID: DAIKIN, ATTR_FAN_MODE: "Low/Auto"})
    assert hass.states.get(DAIKIN).attributes[ATTR_FAN_MODE] == "Low/Auto"

    await _call(hass, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: DAIKIN})
    state = hass.states.get(DAIKIN)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF

    # turn_on restores the previous (heat_cool) mode.
    await _call(hass, SERVICE_TURN_ON, {ATTR_ENTITY_ID: DAIKIN})
    assert hass.states.get(DAIKIN).state == HVACMode.HEAT_COOL


async def test_dual_swing_write_path(hass: HomeAssistant) -> None:
    """Dual-setpoint range, swing and fan writes."""
    await _setup_via_config_entry(hass)

    await _call(
        hass,
        SERVICE_SET_TEMPERATURE,
        {
            ATTR_ENTITY_ID: DUAL,
            ATTR_TARGET_TEMP_LOW: 18,
            ATTR_TARGET_TEMP_HIGH: 21,
        },
    )
    state = hass.states.get(DUAL)
    assert state.attributes[ATTR_TARGET_TEMP_LOW] == 18
    assert state.attributes[ATTR_TARGET_TEMP_HIGH] == 21
    # heat_cool with current 22 > high 21 -> cooling.
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING

    # Raise the band above current 22 -> heating.
    await _call(
        hass,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: DUAL, ATTR_TARGET_TEMP_LOW: 24, ATTR_TARGET_TEMP_HIGH: 28},
    )
    assert hass.states.get(DUAL).attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING

    await _call(hass, SERVICE_SET_SWING_MODE, {ATTR_ENTITY_ID: DUAL, ATTR_SWING_MODE: "both"})
    assert hass.states.get(DUAL).attributes[ATTR_SWING_MODE] == "both"

    await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_ENTITY_ID: DUAL, ATTR_FAN_MODE: "high"})
    assert hass.states.get(DUAL).attributes[ATTR_FAN_MODE] == "high"

    await _call(hass, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: DUAL, "hvac_mode": HVACMode.OFF})
    assert hass.states.get(DUAL).state == STATE_OFF
    assert hass.states.get(DUAL).attributes[ATTR_HVAC_ACTION] == HVACAction.OFF


async def test_heat_cool_auto_write_path(hass: HomeAssistant) -> None:
    """Auto profile: single setpoint, mode switching and action recompute."""
    await _setup_via_config_entry(hass)

    # auto with current 21 < target 23 -> heating after a write.
    await _call(hass, SERVICE_SET_TEMPERATURE, {ATTR_ENTITY_ID: AUTO, ATTR_TEMPERATURE: 23})
    assert hass.states.get(AUTO).attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING

    await _call(hass, SERVICE_SET_TEMPERATURE, {ATTR_ENTITY_ID: AUTO, ATTR_TEMPERATURE: 19})
    state = hass.states.get(AUTO)
    assert state.attributes[ATTR_TEMPERATURE] == 19
    # current 21 > target 19 -> cooling.
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING

    await _call(hass, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: AUTO, "hvac_mode": HVACMode.HEAT_COOL})
    assert hass.states.get(AUTO).state == HVACMode.HEAT_COOL

    await _call(hass, SERVICE_SET_FAN_MODE, {ATTR_ENTITY_ID: AUTO, ATTR_FAN_MODE: "high"})
    assert hass.states.get(AUTO).attributes[ATTR_FAN_MODE] == "high"

    await _call(hass, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: AUTO})
    assert hass.states.get(AUTO).state == STATE_OFF

    # turn_on restores heat_cool (the last active mode).
    await _call(hass, SERVICE_TURN_ON, {ATTR_ENTITY_ID: AUTO})
    assert hass.states.get(AUTO).state == HVACMode.HEAT_COOL


async def test_yaml_import_sets_up_entities(hass: HomeAssistant) -> None:
    """A bare `mock_climate:` YAML key brings the entities up via import."""
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()

    for entity_id in (DAIKIN, DUAL, AUTO):
        assert hass.states.get(entity_id) is not None


async def test_config_flow_creates_single_entry(hass: HomeAssistant) -> None:
    """The UI flow creates the entity-bearing entry and is single-instance."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get(DAIKIN) is not None

    # A second attempt aborts because only one instance is allowed.
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
