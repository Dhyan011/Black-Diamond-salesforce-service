import pytest
import responses
from unittest.mock import MagicMock

from app.clients.bulk_api_client import SalesforceBulkAPIClient, BulkAPIError, JOBS_BASE

@pytest.fixture
def bulk_client(mock_token_manager):
    return SalesforceBulkAPIClient(token_manager=mock_token_manager, timeout=1)

@responses.activate
def test_create_query_job(bulk_client):
    """Test creating a Bulk API query job."""
    url = f"https://test.salesforce.com{JOBS_BASE}"
    
    responses.add(
        responses.POST,
        url,
        json={"id": "job-123", "state": "UploadComplete"},
        status=200
    )
    
    job_id = bulk_client.create_query_job("SELECT Id FROM Contact")
    assert job_id == "job-123"
    
    assert len(responses.calls) == 1
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"

@responses.activate
def test_retry_on_429(bulk_client, mocker):
    """Test exponential backoff on rate limit (429)."""
    mocker.patch("time.sleep")  # Don't actually sleep in tests
    
    url = f"https://test.salesforce.com{JOBS_BASE}"
    
    # Fail once with 429, then succeed
    responses.add(
        responses.POST,
        url,
        json={"error": "Too Many Requests"},
        status=429,
        headers={"Retry-After": "1"}
    )
    responses.add(
        responses.POST,
        url,
        json={"id": "job-123"},
        status=200
    )
    
    job_id = bulk_client.create_query_job("SELECT Id FROM Contact")
    assert job_id == "job-123"
    assert len(responses.calls) == 2

@responses.activate
def test_poll_until_complete(bulk_client, mocker):
    """Test polling job status until completion."""
    mocker.patch("time.sleep")
    
    url = f"https://test.salesforce.com{JOBS_BASE}/job-123"
    
    # InProgress -> JobComplete
    responses.add(
        responses.GET,
        url,
        json={"id": "job-123", "state": "InProgress"},
        status=200
    )
    responses.add(
        responses.GET,
        url,
        json={
            "id": "job-123", 
            "state": "JobComplete",
            "numberRecordsProcessed": 1500
        },
        status=200
    )
    
    result = bulk_client.poll_until_complete("job-123")
    
    assert result.is_success
    assert result.records_processed == 1500
    assert len(responses.calls) == 2
