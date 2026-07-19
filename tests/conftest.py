"""
Salesforce Service — Pytest Fixtures
"""

import os
import pytest
from unittest.mock import MagicMock

from app.main import create_app
from app.config import SalesforceServiceSettings

@pytest.fixture
def mock_settings(monkeypatch):
    """Provide mock settings for tests."""
    monkeypatch.setenv("FLASK_ENV", "testing")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-chars-long-here!")
    monkeypatch.setenv("SF_CONSUMER_KEY", "test-sf-key")
    monkeypatch.setenv("SF_USERNAME", "test@example.com")
    monkeypatch.setenv("SF_PRIVATE_KEY_PEM", "test-pem-data")
    monkeypatch.setenv("HMAC_ENABLED", "false")
    return SalesforceServiceSettings()

@pytest.fixture
def app(mock_settings):
    """Provide a test Flask application."""
    app = create_app()
    app.config.update({
        "TESTING": True,
    })
    return app

@pytest.fixture
def client(app):
    """Provide a test Flask client."""
    return app.test_client()

@pytest.fixture
def mock_token_manager(mocker):
    """Provide a mock SalesforceTokenManager."""
    mock = mocker.patch("app.auth.salesforce_auth.SalesforceTokenManager")
    instance = mock.return_value
    instance.get_token.return_value = ("test-token", "https://test.salesforce.com")
    return instance

@pytest.fixture
def mock_bulk_client(mocker):
    """Provide a mock SalesforceBulkAPIClient."""
    mock = mocker.patch("app.clients.bulk_api_client.SalesforceBulkAPIClient")
    instance = mock.return_value
    return instance

@pytest.fixture
def mock_minio_client(mocker):
    """Provide a mock MinIOClient."""
    mock = mocker.patch("app.storage.minio_client.MinIOClient")
    instance = mock.return_value
    return instance
