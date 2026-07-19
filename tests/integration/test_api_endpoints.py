import pytest
import json

def test_health_check(client):
    """Test the /api/health endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    
    data = json.loads(response.data)
    assert data["status"] == "ok"
    assert data["service"] == "salesforce-service"
    assert "version" in data

def test_start_scan_unauthorized(client, monkeypatch):
    """Test HMAC authorization enforcement."""
    # Enable HMAC for this test
    monkeypatch.setenv("HMAC_ENABLED", "true")
    monkeypatch.setenv("HMAC_SECRET_KEY_CORE", "test-key")
    
    # Missing headers
    response = client.post("/api/scan/start", json={
        "org_id": "123",
        "objects": ["Contact"]
    })
    assert response.status_code == 401
    
    # Bad signature
    response = client.post("/api/scan/start", 
        headers={
            "X-HMAC-Signature": "bad-signature",
            "X-HMAC-Timestamp": "1234567890"
        },
        json={
            "org_id": "123",
            "objects": ["Contact"]
        }
    )
    assert response.status_code == 401

def test_start_scan_success(client, mocker):
    """Test starting a scan successfully."""
    # Mock the extraction service to avoid actual API calls
    mocker.patch("app.routes.extraction_service.start_scan")
    
    response = client.post("/api/scan/start", json={
        "org_id": "org-123",
        "objects": ["Contact", "Account"]
    })
    
    assert response.status_code == 202
    data = json.loads(response.data)
    assert data["status"] == "Scan initiated"
    assert "scan_id" in data

def test_start_scan_validation_error(client):
    """Test payload validation for scan start."""
    # Missing required fields
    response = client.post("/api/scan/start", json={
        "objects": ["Contact"]
    })
    
    assert response.status_code == 400
    assert "Input payload validation failed" in str(response.data)
