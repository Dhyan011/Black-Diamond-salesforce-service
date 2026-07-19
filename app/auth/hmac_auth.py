"""
Salesforce Service — HMAC Authentication

Dual-key HMAC signature generation and verification for internal API calls.
Keys: core-key (for core-service) and engineer-key (for admin operations).

Signature format:
    HMAC-SHA256(secret_key, "{method}\n{path}\n{timestamp}\n{body}")

Request headers:
    X-HMAC-Signature: <hex digest>
    X-HMAC-Timestamp: <unix timestamp>
    X-Service-ID: <caller identifier>
"""

import hashlib
import hmac
import time
import logging

logger = logging.getLogger(__name__)


def generate_hmac_signature(
    secret_key: str,
    method: str,
    path: str,
    body: str = "",
    timestamp: str = None,
) -> tuple[str, str]:
    """
    Generate an HMAC-SHA256 signature for an API request.

    Args:
        secret_key: The shared HMAC secret key.
        method: HTTP method (GET, POST, etc.).
        path: Request path (e.g., /api/scan/start).
        body: Request body (empty string for GET requests).
        timestamp: Unix timestamp string. Auto-generated if not provided.

    Returns:
        Tuple of (signature_hex, timestamp_str).
    """
    if timestamp is None:
        timestamp = str(int(time.time()))

    # Build the message to sign
    message = f"{method.upper()}\n{path}\n{timestamp}\n{body}"

    # Generate HMAC-SHA256
    signature = hmac.new(
        key=secret_key.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return signature, timestamp


def verify_hmac_signature(
    secret_key: str,
    signature: str,
    timestamp: str,
    method: str,
    path: str,
    body: str = "",
    max_age: int = 300,
) -> bool:
    """
    Verify an HMAC-SHA256 signature from an incoming request.

    Checks:
    1. Timestamp is within max_age seconds of current time (replay protection).
    2. Signature matches the expected HMAC digest.

    Args:
        secret_key: The shared HMAC secret key.
        signature: The signature from X-HMAC-Signature header.
        timestamp: The timestamp from X-HMAC-Timestamp header.
        method: HTTP method (GET, POST, etc.).
        path: Request path.
        body: Request body.
        max_age: Maximum allowed age in seconds (default: 300).

    Returns:
        True if the signature is valid and not expired.
    """
    # Check timestamp freshness (replay protection)
    try:
        request_time = int(timestamp)
    except (ValueError, TypeError):
        logger.warning("HMAC verification failed: invalid timestamp format")
        return False

    current_time = int(time.time())
    age = abs(current_time - request_time)

    if age > max_age:
        logger.warning(
            f"HMAC verification failed: signature expired "
            f"(age={age}s, max_age={max_age}s)"
        )
        return False

    # Regenerate expected signature
    expected_signature, _ = generate_hmac_signature(
        secret_key=secret_key,
        method=method,
        path=path,
        body=body,
        timestamp=timestamp,
    )

    # Constant-time comparison to prevent timing attacks
    is_valid = hmac.compare_digest(signature, expected_signature)

    if not is_valid:
        logger.warning("HMAC verification failed: signature mismatch")

    return is_valid


def generate_request_headers(
    secret_key: str,
    service_id: str,
    method: str,
    path: str,
    body: str = "",
) -> dict[str, str]:
    """
    Generate HMAC authentication headers for an outgoing request.

    Convenience method for service-to-service calls.

    Args:
        secret_key: The shared HMAC secret key.
        service_id: Identifier of the calling service.
        method: HTTP method.
        path: Request path.
        body: Request body.

    Returns:
        Dictionary of headers to include in the request.
    """
    signature, timestamp = generate_hmac_signature(
        secret_key=secret_key,
        method=method,
        path=path,
        body=body,
    )

    return {
        "X-HMAC-Signature": signature,
        "X-HMAC-Timestamp": timestamp,
        "X-Service-ID": service_id,
    }
