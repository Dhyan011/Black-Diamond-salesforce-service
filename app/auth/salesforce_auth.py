"""
Salesforce Service — Salesforce Token Manager

OAuth 2.0 JWT Bearer flow with automatic token refresh.
Implements the authentication pattern from Section 3.1 of the Technical Design Document.

The JWT Bearer Token flow:
1. Build a JWT with iss (consumer key), sub (username), aud (login URL), exp
2. Sign with RSA private key
3. Exchange JWT for access_token at Salesforce OAuth endpoint
4. Cache token and auto-refresh 5 minutes before expiry
"""

import time
import base64
import logging
from threading import Lock
from typing import Optional

import jwt
import requests

logger = logging.getLogger(__name__)


class SalesforceTokenManager:
    """
    Manages OAuth 2.0 JWT Bearer tokens for Salesforce API access.

    Thread-safe: uses a Lock to ensure only one thread refreshes at a time.
    Auto-refreshes tokens 5 minutes (300s) before expiry.
    """

    TOKEN_LIFETIME_SECONDS = 7200  # ~2 hours
    REFRESH_BUFFER_SECONDS = 300   # Refresh 5 minutes before expiry
    JWT_EXPIRY_SECONDS = 180       # JWT assertion valid for 3 minutes

    def __init__(
        self,
        consumer_key: str,
        private_key_pem: str,
        username: str,
        login_url: str,
        timeout: int = 30,
    ):
        """
        Initialize the token manager.

        Args:
            consumer_key: Salesforce Connected App consumer key.
            private_key_pem: RSA private key in PEM format (or base64-encoded PEM).
            username: Salesforce integration user email.
            login_url: Salesforce login URL (login.salesforce.com or test.salesforce.com).
            timeout: HTTP request timeout in seconds.
        """
        self._consumer_key = consumer_key
        self._private_key = self._decode_private_key(private_key_pem)
        self._username = username
        self._login_url = login_url.rstrip("/")
        self._timeout = timeout

        # Cached token state
        self._access_token: Optional[str] = None
        self._instance_url: Optional[str] = None
        self._expires_at: float = 0

        # Thread safety
        self._lock = Lock()

    @staticmethod
    def _decode_private_key(key_data: str) -> str:
        """
        Decode the private key from PEM or base64-encoded PEM.

        Vault may store the PEM key as base64 to avoid newline issues.
        """
        if not key_data:
            return ""

        # If it already looks like a PEM key, return as-is
        if "-----BEGIN" in key_data:
            return key_data

        # Try base64 decoding
        try:
            decoded = base64.b64decode(key_data).decode("utf-8")
            if "-----BEGIN" in decoded:
                return decoded
        except Exception:
            pass

        # Return as-is and let PyJWT handle the error
        return key_data

    def get_token(self) -> tuple[str, str]:
        """
        Returns (access_token, instance_url), refreshing if within 5 minutes of expiry.

        Thread-safe: only one thread will refresh at a time.

        Returns:
            Tuple of (access_token, instance_url).

        Raises:
            SalesforceAuthError: If authentication fails.
        """
        with self._lock:
            if time.time() > (self._expires_at - self.REFRESH_BUFFER_SECONDS):
                self._refresh()
        return self._access_token, self._instance_url

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid (non-expired) token."""
        return (
            self._access_token is not None
            and time.time() < self._expires_at
        )

    @property
    def token_expires_in(self) -> int:
        """Seconds until the current token expires."""
        remaining = self._expires_at - time.time()
        return max(0, int(remaining))

    def _refresh(self) -> None:
        """
        Exchange a JWT assertion for a new Salesforce access token.

        Step 1: Build and sign JWT
        Step 2: POST to Salesforce OAuth endpoint
        Step 3: Cache access_token and instance_url
        """
        now = int(time.time())

        # Step 1: Build JWT claim
        claim = {
            "iss": self._consumer_key,
            "sub": self._username,
            "aud": self._login_url,
            "exp": now + self.JWT_EXPIRY_SECONDS,
        }

        # Sign with RSA private key
        try:
            signed_jwt = jwt.encode(
                claim,
                self._private_key,
                algorithm="RS256",
            )
        except Exception as e:
            logger.error(f"Failed to sign JWT: {e}")
            raise SalesforceAuthError(f"JWT signing failed: {e}") from e

        # Step 2: Exchange JWT for access token
        token_url = f"{self._login_url}/services/oauth2/token"
        try:
            resp = requests.post(
                token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Salesforce OAuth token exchange failed: {e}")
            raise SalesforceAuthError(
                f"Token exchange failed: {e}"
            ) from e

        data = resp.json()

        # Step 3: Cache the token
        self._access_token = data["access_token"]
        self._instance_url = data["instance_url"]
        self._expires_at = now + self.TOKEN_LIFETIME_SECONDS

        logger.info(
            f"Salesforce token refreshed. Instance: {self._instance_url}. "
            f"Expires in {self.TOKEN_LIFETIME_SECONDS}s."
        )

    def invalidate(self) -> None:
        """Force token refresh on next access."""
        with self._lock:
            self._expires_at = 0
            self._access_token = None
            logger.info("Salesforce token invalidated. Will refresh on next access.")


class SalesforceAuthError(Exception):
    """Raised when Salesforce authentication fails."""
    pass
