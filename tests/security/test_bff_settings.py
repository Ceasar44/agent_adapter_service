import pytest
from pydantic import ValidationError

from agent_adapter_service.core.settings import Settings
from tests.security.test_storefront_bff import VECTOR


@pytest.mark.parametrize("values", [
    {"agui_enabled": True}, {"bff_keys": {"bad.id": VECTOR["key"]}},
    {"bff_keys": {"active": "c2hvcnQ="}}, {"bff_keys": {"active": "not-base64"}},
    {"bff_clock_skew_seconds": 6}, {"bff_max_age_seconds": 61},
])
def test_invalid_auth_configuration(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_keys_are_redacted_and_rotation_supported():
    config = Settings(_env_file=None, bff_keys={"active": VECTOR["key"], "previous": VECTOR["key"]})
    assert VECTOR["key"] not in repr(config)
    assert VECTOR["key"] not in config.model_dump_json()
