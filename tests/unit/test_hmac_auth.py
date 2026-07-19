import time
from app.auth.hmac_auth import generate_hmac_signature, verify_hmac_signature, generate_request_headers

SECRET = "super-secret-key-123"

def test_generate_and_verify_signature():
    """Test standard HMAC generation and verification."""
    method = "POST"
    path = "/api/scan/start"
    body = '{"org_id":"123","objects":["Contact"]}'
    
    signature, timestamp = generate_hmac_signature(
        secret_key=SECRET,
        method=method,
        path=path,
        body=body
    )
    
    is_valid = verify_hmac_signature(
        secret_key=SECRET,
        signature=signature,
        timestamp=timestamp,
        method=method,
        path=path,
        body=body
    )
    
    assert is_valid is True

def test_verify_fails_on_tampering():
    """Test that tampering with any input invalidates the signature."""
    method = "POST"
    path = "/api/scan/start"
    body = '{"org_id":"123"}'
    
    signature, timestamp = generate_hmac_signature(SECRET, method, path, body)
    
    # Tamper with body
    assert not verify_hmac_signature(SECRET, signature, timestamp, method, path, body='{"org_id":"456"}')
    
    # Tamper with path
    assert not verify_hmac_signature(SECRET, signature, timestamp, method, "/api/scan/stop", body)
    
    # Tamper with signature
    assert not verify_hmac_signature(SECRET, "bad" + signature, timestamp, method, path, body)
    
    # Wrong secret
    assert not verify_hmac_signature("wrong-secret", signature, timestamp, method, path, body)

def test_replay_protection():
    """Test that expired timestamps are rejected."""
    method = "GET"
    path = "/api/health"
    
    # Generate timestamp from 10 minutes ago
    old_timestamp = str(int(time.time()) - 600)
    
    signature, _ = generate_hmac_signature(SECRET, method, path, timestamp=old_timestamp)
    
    # Verify with default 5-minute max_age
    is_valid = verify_hmac_signature(
        secret_key=SECRET,
        signature=signature,
        timestamp=old_timestamp,
        method=method,
        path=path,
        max_age=300
    )
    
    assert is_valid is False

def test_generate_headers():
    """Test header generation convenience function."""
    headers = generate_request_headers(
        secret_key=SECRET,
        service_id="core-service",
        method="GET",
        path="/api/health"
    )
    
    assert "X-HMAC-Signature" in headers
    assert "X-HMAC-Timestamp" in headers
    assert headers["X-Service-ID"] == "core-service"
