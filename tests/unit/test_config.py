import pytest
from pydantic import ValidationError

from app.config import SalesforceServiceSettings, validate_settings

def test_settings_validation_success(monkeypatch):
    """Test successful settings validation with required fields."""
    monkeypatch.setenv("FLASK_ENV", "testing")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-chars-long-here!")
    monkeypatch.setenv("SF_CONSUMER_KEY", "test-sf-key")
    monkeypatch.setenv("SF_USERNAME", "test@example.com")
    monkeypatch.setenv("SF_PRIVATE_KEY_PEM", "test-pem-data")
    monkeypatch.setenv("HMAC_ENABLED", "false")
    
    settings = validate_settings()
    
    assert settings.FLASK_ENV == "testing"
    assert settings.SF_CONSUMER_KEY == "test-sf-key"
    assert settings.HMAC_ENABLED is False

def test_settings_validation_failure(monkeypatch):
    """Test settings validation fails when missing required fields."""
    monkeypatch.delenv("SF_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("SF_USERNAME", raising=False)
    
    with pytest.raises(SystemExit) as e:
        validate_settings()
        
    assert e.type == SystemExit
    assert e.value.code == 1

def test_hmac_requirements(monkeypatch):
    """Test HMAC validation logic."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-chars-long-here!")
    monkeypatch.setenv("SF_CONSUMER_KEY", "test-sf-key")
    monkeypatch.setenv("SF_USERNAME", "test@example.com")
    monkeypatch.setenv("SF_PRIVATE_KEY_PEM", "test-pem-data")
    monkeypatch.setenv("HMAC_ENABLED", "true")
    
    # Fails without keys
    with pytest.raises(SystemExit) as e:
        validate_settings()
    assert e.value.code == 1
    
    # Succeeds with keys
    monkeypatch.setenv("HMAC_SECRET_KEY_CORE", "core-key")
    monkeypatch.setenv("HMAC_SECRET_KEY_ENGINEER", "engineer-key")
    
    settings = validate_settings()
    assert settings.HMAC_ENABLED is True
    assert settings.HMAC_SECRET_KEY_CORE == "core-key"
