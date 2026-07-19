import pytest
import pyarrow.parquet as pq
import io

from app.services.normalization_service import NormalizationService

def test_csv_records_to_parquet():
    """Test converting simple dict records to Parquet."""
    service = NormalizationService()
    
    records = [
        {"Id": "001", "Name": "Alice", "IsActive": "true"},
        {"Id": "002", "Name": "Bob", "IsActive": "false"},
    ]
    
    parquet_bytes = service.csv_records_to_parquet(records)
    assert len(parquet_bytes) > 0
    
    # Verify metadata
    metadata = service.get_parquet_metadata(parquet_bytes)
    assert metadata["num_rows"] == 2
    assert "Id" in metadata["columns"]
    assert "Name" in metadata["columns"]
    assert "IsActive" in metadata["columns"]

def test_empty_records():
    """Test converting empty records returns valid minimal Parquet."""
    service = NormalizationService()
    parquet_bytes = service.csv_records_to_parquet([])
    
    metadata = service.get_parquet_metadata(parquet_bytes)
    assert metadata["num_rows"] == 0
    assert metadata["columns"] == ["_empty"]

def test_type_coercion():
    """Test Salesforce-specific type coercion."""
    service = NormalizationService()
    
    records = [
        {"Id": "001", "CreatedDate": "2023-01-01T12:00:00Z", "IsDeleted": "false"},
        {"Id": "002", "CreatedDate": "2023-01-02T12:00:00Z", "IsDeleted": "true"},
    ]
    
    parquet_bytes = service.csv_records_to_parquet(records)
    
    # Read back to check types
    table = pq.read_table(io.BytesIO(parquet_bytes))
    schema = table.schema
    
    assert str(schema.field("CreatedDate").type).startswith("timestamp")
    assert str(schema.field("IsDeleted").type) == "bool"
    assert str(schema.field("Id").type) == "string"
