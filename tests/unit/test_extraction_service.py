import pytest
from unittest.mock import MagicMock

from app.services.extraction_service import ExtractionService
from app.models.scan import ScanStatus

@pytest.fixture
def extraction_service(mock_token_manager, mock_bulk_client, mock_minio_client):
    return ExtractionService(
        token_manager=mock_token_manager,
        bulk_client=mock_bulk_client,
        minio_client=mock_minio_client,
        supported_objects=[{"name": "Contact", "default_fields": ["Id", "Name"]}],
    )

def test_start_scan(extraction_service, mocker):
    """Test starting a scan initializes state correctly."""
    mocker.patch.object(extraction_service, "_run_extraction")
    
    scan = extraction_service.start_scan(
        scan_id="scan-123",
        org_id="org-456",
        objects=["Contact"]
    )
    
    assert scan.scan_id == "scan-123"
    assert scan.org_id == "org-456"
    assert scan.status == ScanStatus.STARTED
    assert "Contact" in scan.progress
    
    # Ensure it was tracked in memory
    assert extraction_service.get_scan("scan-123") == scan

def test_cancel_scan(extraction_service, mocker):
    """Test cancelling an active scan."""
    mocker.patch.object(extraction_service, "_run_extraction")
    
    extraction_service.start_scan(
        scan_id="scan-123",
        org_id="org-456",
        objects=["Contact"]
    )
    
    cancelled_scan = extraction_service.cancel_scan("scan-123")
    assert cancelled_scan.status == ScanStatus.CANCELLED
    assert cancelled_scan.progress["Contact"].state == "Aborted"

def test_build_soql(extraction_service):
    """Test SOQL query building with filters."""
    # Test simple query
    soql = extraction_service._build_soql("Contact")
    assert soql == "SELECT Id, Name FROM Contact"
    
    # Test fallback for unknown object
    soql = extraction_service._build_soql("UnknownObj")
    assert soql == "SELECT Id FROM UnknownObj"
