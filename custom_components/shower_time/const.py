"""Constants for Hot Water Shower Time."""
DOMAIN = "shower_time"

CONF_TOP = "top_sensor"
CONF_BOTTOM = "bottom_sensor"
CONF_VOLUME = "tank_volume"
CONF_SHOWER_TEMP = "shower_temperature"
CONF_PROFILE = "profile"
CONF_HEATING = "heating_entity"
CONF_HEATING_STATES = "heating_states"
CONF_ENERGY = "energy_sensor"
CONF_LOW = "low_minutes"

DEFAULT_NAME = "Hot water"
DEFAULT_VOLUME = 200
DEFAULT_SHOWER_TEMP = 38
DEFAULT_LOW = 10
DEFAULT_HEATING_STATES = "dhw, legionella_prevention, heating_dhw, on, heat"
PROFILES = ["generic", "weheat_200"]

LEARN_HOUR, LEARN_MINUTE = 3, 45
HISTORY_DAYS = 10
STORAGE_VERSION = 1

CARD_URL = "/shower_time/shower-time-card.js"

# Shower temperature: learned automatically (heat per minute) or set by the user.
CONF_TEMP_MODE = "temperature_mode"
TEMP_MODES = ["auto", "fixed"]
REF_TEMP = 38.0                      # reference temperature for the litres in auto mode

# Optional shower-head flow, to turn the learned heat per minute into a real shower temperature.
CONF_FLOW_SOURCE = "flow_source"
FLOW_SOURCES = ["unknown", "water_meter", "eco", "normal", "rain", "manual"]
HEAD_PRESETS = {"eco": 6.0, "normal": 8.0, "rain": 12.0}
CONF_HEAD_FLOW = "head_flow"
CONF_WATER = "water_sensor"
WATER_MIN_OBSERVATIONS = 3

# Optional outdoor temperature: mains water follows a ~3-week running average of it
CONF_OUTDOOR = "outdoor_sensor"
COLD_TAU_DAYS = 21
