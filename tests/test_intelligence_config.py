"""Environment parsing must accept OFF and refuse unreviewed gating."""
import pytest
from pydantic import ValidationError

from act.intelligence import IntelligenceSettings


@pytest.mark.parametrize("value", ["false", "False", "0", "off"])
def test_explicit_off_environment(monkeypatch, value):
    monkeypatch.setenv("INTELLIGENCE_GATES_ENABLED", value)
    assert IntelligenceSettings().gates_enabled is False


@pytest.mark.parametrize("value", ["true", "1", "on"])
def test_on_environment_is_refused(monkeypatch, value):
    monkeypatch.setenv("INTELLIGENCE_GATES_ENABLED", value)
    with pytest.raises(ValidationError, match="keep OFF"):
        IntelligenceSettings()
