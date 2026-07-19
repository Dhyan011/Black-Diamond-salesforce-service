"""
Salesforce Service — Services Package

Business logic layer:
- extraction_service: Orchestrates the full scan lifecycle
- polling_service: Async polling loop with adaptive intervals
- normalization_service: CSV → Parquet conversion
- deduplication_service: Remove duplicate records by Salesforce Id
- maintenance_service: Cleanup old scans and MinIO objects
"""
