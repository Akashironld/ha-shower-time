"""Load the Home Assistant test plugin when it is installed (the HA level tests in tests/ha need it; the model
tests in tests/ run without it)."""
try:
    import pytest_homeassistant_custom_component  # noqa: F401
except ImportError:
    pass
else:
    pytest_plugins = ["pytest_homeassistant_custom_component"]
