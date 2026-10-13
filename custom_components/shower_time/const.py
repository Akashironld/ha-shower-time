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
