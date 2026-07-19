"""
Salesforce Service — Configuration Module

Pydantic-based settings with startup validation.
All environment variables from Section 6.1 of the Technical Design Document.
"""

import os
import logging
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class SalesforceServiceSettings(BaseSettings):
    """
    All configuration for the Salesforce Service.
    Values are loaded from environment variables (injected by Nomad/Vault in production).
    """

    # ---- Flask ----
    APP_VERSION: str = Field(default="1.0.0", description="Application version")
    APP_TITLE: str = Field(default="Salesforce Service", description="Application title")
    FLASK_ENV: str = Field(default="development", description="Flask environment")
    FLASK_DEBUG: bool = Field(default=False, description="Flask debug mode")
    SECRET_KEY: str = Field(default="change-me-in-production", description="Flask secret key")
    PORT: int = Field(default=5710, description="Service listen port")
    HOST: str = Field(default="0.0.0.0", description="Service bind address")
    ENVIRONMENT: str = Field(default="dev", description="Deployment environment: dev, stage, prod")

    # ---- Logging ----
    LOG_LEVEL: str = Field(default="DEBUG", description="Log verbosity")
    LOG_FORMAT: str = Field(default="text", description="Log format: text or json")
    LOKI_ENABLED: bool = Field(default=False, description="Enable Loki log shipping")

    # ---- Database (PostgreSQL) ----
    DB_HOST: str = Field(default="localhost", description="PostgreSQL host")
    DB_PORT: int = Field(default=5432, description="PostgreSQL port")
    DB_NAME: str = Field(default="salesforce_dev", description="Database name")
    DB_USER: str = Field(default="dev_user", description="Database user")
    DB_PASSWORD: str = Field(default="dev_pass", description="Database password")
    DB_SCHEMA: str = Field(default="public", description="Database schema")

    # ---- Salesforce ----
    SF_CONSUMER_KEY: str = Field(default="", description="Salesforce OAuth consumer key")
    SF_PRIVATE_KEY_PEM: str = Field(default="", description="RSA private key for JWT signing (PEM or base64)")
    SF_USERNAME: str = Field(default="", description="Salesforce integration user")
    SF_LOGIN_URL: str = Field(default="https://test.salesforce.com", description="Salesforce login URL")
    SF_API_VERSION: str = Field(default="v59.0", description="Salesforce API version")
    SF_BULK_PAGE_SIZE: int = Field(default=50000, description="Records per Bulk API result page")
    SF_MAX_JOB_TIMEOUT_HOURS: int = Field(default=2, description="Abort stuck jobs after N hours")

    # ---- Scan Limits ----
    MAX_CONCURRENT_SCANS: int = Field(default=2, description="Max parallel scans")
    SCAN_TIMEOUT_HOURS: int = Field(default=2, description="Max scan duration in hours")
    CLEANUP_DAYS: int = Field(default=7, description="Retain scan records for N days")

    # ---- HMAC Authentication ----
    HMAC_ENABLED: bool = Field(default=False, description="Enable HMAC auth for internal calls")
    HMAC_SECRET_KEY_CORE: str = Field(default="", description="Core-service HMAC shared secret")
    HMAC_SECRET_KEY_ENGINEER: str = Field(default="", description="Engineer HMAC shared secret")
    HMAC_SIGNATURE_MAX_AGE: int = Field(default=300, description="Signature TTL in seconds")

    # ---- MinIO ----
    MINIO_ENABLED: bool = Field(default=False, description="Enable MinIO storage")
    MINIO_ENDPOINT: str = Field(default="localhost:9000", description="MinIO endpoint")
    MINIO_ACCESS_KEY: str = Field(default="minioadmin", description="MinIO access key")
    MINIO_SECRET_KEY: str = Field(default="minioadmin", description="MinIO secret key")
    MINIO_SECURE: bool = Field(default=False, description="Use TLS for MinIO")
    MINIO_BUCKET: str = Field(default="salesforce-dev", description="Default MinIO bucket")

    # ---- PII Masking ----
    PII_MASKING_ENABLED: bool = Field(default=False, description="Enable PII masking")
    PII_SERVICE_URL: str = Field(default="http://localhost:8100", description="PII service URL")
    PII_HMAC_KEY: str = Field(default="", description="PII service HMAC key")
    PII_SERVICE_ID: str = Field(default="sf-service-dev", description="PII service identifier")

    # ---- CORS ----
    ALLOWED_ORIGINS: str = Field(default="*", description="CORS allowed origins")

    # ---- Core Service ----
    CORE_SERVICE_URL: str = Field(default="http://localhost:5700", description="Core service URL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"dev", "stage", "prod"}
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}, got '{v}'")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}, got '{v}'")
        return v

    @field_validator("LOG_FORMAT")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        allowed = {"text", "json"}
        if v not in allowed:
            raise ValueError(f"LOG_FORMAT must be one of {allowed}, got '{v}'")
        return v

    @property
    def database_url(self) -> str:
        """Construct PostgreSQL connection URL."""
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into a list."""
        if self.ALLOWED_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


def validate_settings() -> dict[str, dict[str, str]]:
    """
    Validate all settings at startup. Returns a dict of errors (empty if valid).

    This implements the fail-fast pattern from CLAUDE.md:
    the service must validate all configuration before binding to the network port.
    """
    errors: dict[str, dict[str, str]] = {}

    try:
        settings = SalesforceServiceSettings()
    except Exception as e:
        errors["SETTINGS_LOAD"] = {
            "error": str(e),
            "fix": "Check all environment variables are set correctly.",
        }
        return errors

    env = settings.ENVIRONMENT

    # ---- Production/Staging-specific validations ----
    if env in ("stage", "prod"):
        # Flask debug must be off
        if settings.FLASK_DEBUG:
            errors["FLASK_DEBUG"] = {
                "error": f"FLASK_DEBUG is True in {env} environment",
                "fix": "Set FLASK_DEBUG=False for staging/production.",
            }

        # HMAC must be enabled
        if not settings.HMAC_ENABLED:
            errors["HMAC_ENABLED"] = {
                "error": f"HMAC authentication is disabled in {env}",
                "fix": "Set HMAC_ENABLED=true for staging/production.",
            }

        # HMAC keys must be 32+ characters
        if settings.HMAC_ENABLED:
            if len(settings.HMAC_SECRET_KEY_CORE) < 32:
                errors["HMAC_SECRET_KEY_CORE"] = {
                    "error": "HMAC core key must be at least 32 characters",
                    "fix": "Generate with: openssl rand -hex 32",
                }
            if len(settings.HMAC_SECRET_KEY_ENGINEER) < 32:
                errors["HMAC_SECRET_KEY_ENGINEER"] = {
                    "error": "HMAC engineer key must be at least 32 characters",
                    "fix": "Generate with: openssl rand -hex 32",
                }

        # SECRET_KEY must be 32+ characters
        if len(settings.SECRET_KEY) < 32:
            errors["SECRET_KEY"] = {
                "error": "SECRET_KEY must be at least 32 characters in production",
                "fix": "Generate with: openssl rand -hex 32",
            }

        # CORS must not be wildcard
        if settings.ALLOWED_ORIGINS == "*":
            errors["ALLOWED_ORIGINS"] = {
                "error": f"Wildcard CORS origins not allowed in {env}",
                "fix": "Set ALLOWED_ORIGINS to specific HTTPS domains.",
            }

        # MinIO must use TLS
        if settings.MINIO_ENABLED and not settings.MINIO_SECURE:
            errors["MINIO_SECURE"] = {
                "error": f"MinIO TLS is disabled in {env}",
                "fix": "Set MINIO_SECURE=true for staging/production.",
            }

        # PII masking should be enabled
        if not settings.PII_MASKING_ENABLED:
            errors["PII_MASKING_ENABLED"] = {
                "error": f"PII masking is disabled in {env}",
                "fix": "Set PII_MASKING_ENABLED=true for staging/production.",
            }

    # ---- Production-specific validations ----
    if env == "prod":
        # Login URL must be production
        if "test.salesforce.com" in settings.SF_LOGIN_URL:
            errors["SF_LOGIN_URL"] = {
                "error": "SF_LOGIN_URL points to sandbox in production",
                "fix": "Set SF_LOGIN_URL=https://login.salesforce.com for production.",
            }

        # Log level should be WARNING or higher
        if settings.LOG_LEVEL in ("DEBUG", "INFO"):
            errors["LOG_LEVEL"] = {
                "error": f"LOG_LEVEL={settings.LOG_LEVEL} is too verbose for production",
                "fix": "Set LOG_LEVEL=WARNING for production.",
            }

    # ---- Salesforce credential checks (all environments) ----
    if not settings.SF_CONSUMER_KEY:
        errors["SF_CONSUMER_KEY"] = {
            "error": "SF_CONSUMER_KEY is not set",
            "fix": "Set the Connected App consumer key from Salesforce Setup.",
        }

    if not settings.SF_USERNAME:
        errors["SF_USERNAME"] = {
            "error": "SF_USERNAME is not set",
            "fix": "Set the Salesforce integration user email.",
        }

    # Check for placeholder values
    for field_name in [
        "SECRET_KEY", "SF_CONSUMER_KEY", "SF_PRIVATE_KEY_PEM",
        "HMAC_SECRET_KEY_CORE", "HMAC_SECRET_KEY_ENGINEER",
    ]:
        value = getattr(settings, field_name, "")
        if value and "REPLACE_WITH" in value.upper():
            errors[field_name] = {
                "error": f"{field_name} contains a placeholder value",
                "fix": f"Replace {field_name} with a real value.",
            }

    return errors


def get_settings() -> SalesforceServiceSettings:
    """Get the current settings instance."""
    return SalesforceServiceSettings()
