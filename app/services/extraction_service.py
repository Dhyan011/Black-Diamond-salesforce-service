"""
Salesforce Service — Extraction Service

Orchestrates the full scan lifecycle (Section 10.1 of the Technical Design Document):
1. Authenticate to Salesforce
2. Create Bulk API jobs for each requested object (parallelized)
3. Poll each job for completion
4. Download results in paginated CSV chunks
5. Per page: parse CSV → PII masking → deduplicate → convert to Parquet → upload to MinIO
6. Cleanup Salesforce jobs
7. Report completion to Core Service
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from app.auth.salesforce_auth import SalesforceTokenManager
from app.clients.bulk_api_client import SalesforceBulkAPIClient, BulkAPIError
from app.models.scan import ScanState, ScanStatus, ObjectProgress
from app.services.normalization_service import NormalizationService
from app.services.deduplication_service import DeduplicationService

logger = logging.getLogger(__name__)


class ExtractionService:
    """
    Orchestrates Salesforce data extraction scans.

    Each scan extracts one or more Salesforce objects using the Bulk API 2.0.
    Objects are processed in parallel using a thread pool.
    """

    def __init__(
        self,
        token_manager: SalesforceTokenManager,
        bulk_client: SalesforceBulkAPIClient,
        minio_client=None,
        normalization_service: Optional[NormalizationService] = None,
        deduplication_service: Optional[DeduplicationService] = None,
        max_workers: int = 3,
        supported_objects: Optional[list[dict]] = None,
    ):
        """
        Initialize the extraction service.

        Args:
            token_manager: Salesforce token manager for auth.
            bulk_client: Bulk API 2.0 client.
            minio_client: MinIO client for storage (optional for testing).
            normalization_service: CSV → Parquet converter.
            deduplication_service: Record deduplicator.
            max_workers: Max parallel object extractions per scan.
            supported_objects: List of supported Salesforce object configs.
        """
        self._token_manager = token_manager
        self._bulk_client = bulk_client
        self._minio_client = minio_client
        self._normalizer = normalization_service or NormalizationService()
        self._deduplicator = deduplication_service or DeduplicationService()
        self._max_workers = max_workers
        self._supported_objects = {
            obj["name"]: obj for obj in (supported_objects or [])
        }

        # Active scans tracked in memory
        self._scans: dict[str, ScanState] = {}
        self._lock = threading.Lock()

    def start_scan(
        self,
        scan_id: str,
        org_id: str,
        objects: list[str],
        filters: Optional[dict] = None,
        output_format: str = "parquet",
        destination: Optional[dict] = None,
    ) -> ScanState:
        """
        Start a new extraction scan.

        Creates a ScanState, launches Bulk API jobs for each object,
        and begins polling/downloading in background threads.

        Args:
            scan_id: Unique scan identifier (from core-service).
            org_id: Organization identifier.
            objects: List of Salesforce object names to extract.
            filters: Optional extraction filters (e.g., last_modified_after).
            output_format: Output format ('parquet' or 'json').
            destination: Optional destination config.

        Returns:
            ScanState with initial progress.
        """
        # Create scan state
        scan = ScanState.create(
            scan_id=scan_id,
            org_id=org_id,
            objects=objects,
            filters=filters,
            output_format=output_format,
            destination=destination,
        )

        with self._lock:
            self._scans[scan_id] = scan

        # Start extraction in background
        thread = threading.Thread(
            target=self._run_extraction,
            args=(scan,),
            name=f"scan-{scan_id}",
            daemon=True,
        )
        thread.start()

        logger.info(
            f"Scan {scan_id} started for org {org_id}, "
            f"objects: {objects}"
        )
        return scan

    def _run_extraction(self, scan: ScanState) -> None:
        """
        Run the full extraction pipeline for a scan.
        Executes in a background thread.
        """
        try:
            scan.status = ScanStatus.IN_PROGRESS
            scan.update_timestamp()

            # Process objects in parallel
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                futures = {}
                for obj_name in scan.objects:
                    future = executor.submit(
                        self._extract_object,
                        scan=scan,
                        object_name=obj_name,
                    )
                    futures[future] = obj_name

                for future in as_completed(futures):
                    obj_name = futures[future]
                    try:
                        future.result()
                        logger.info(
                            f"Scan {scan.scan_id}: {obj_name} extraction complete"
                        )
                    except Exception as e:
                        logger.error(
                            f"Scan {scan.scan_id}: {obj_name} extraction failed: {e}"
                        )
                        progress = scan.progress.get(obj_name)
                        if progress:
                            progress.mark_failed(str(e))

            # Check overall completion
            scan.check_completion()
            logger.info(
                f"Scan {scan.scan_id} finished with status: {scan.status.value}"
            )

        except Exception as e:
            logger.error(f"Scan {scan.scan_id} failed: {e}")
            scan.status = ScanStatus.FAILED
            scan.error_message = str(e)
            scan.update_timestamp()

    def _extract_object(self, scan: ScanState, object_name: str) -> None:
        """
        Extract a single Salesforce object:
        1. Build SOQL query
        2. Create Bulk API job
        3. Poll until complete
        4. Download paginated results
        5. Normalize and upload each page
        6. Cleanup
        """
        progress = scan.progress.get(object_name)
        if not progress:
            raise ValueError(f"No progress tracker for object {object_name}")

        # Step 1: Build SOQL query
        soql = self._build_soql(object_name, scan.filters)
        logger.info(f"Scan {scan.scan_id}/{object_name}: SOQL = {soql}")

        # Step 2: Create Bulk API job
        try:
            job_id = self._bulk_client.create_query_job(soql)
            progress.mark_started(job_id)
            scan.update_timestamp()
        except BulkAPIError as e:
            progress.mark_failed(f"Job creation failed: {e}")
            return

        # Step 3: Poll until complete
        try:
            result = self._bulk_client.poll_until_complete(job_id)
            if not result.is_success:
                progress.mark_failed(
                    f"Job {result.state}: {result.error_message or 'Unknown error'}"
                )
                return
        except BulkAPIError as e:
            progress.mark_failed(f"Polling failed: {e}")
            return

        # Step 4-5: Download and process paginated results
        total_records = 0
        page_count = 0
        minio_prefix = None

        try:
            for page_records in self._bulk_client.iter_results(
                job_id,
                page_size=50000,
            ):
                page_count += 1

                # Deduplicate
                deduped = self._deduplicator.deduplicate(page_records)

                # Normalize to Parquet
                parquet_bytes = self._normalizer.csv_records_to_parquet(deduped)

                # Upload to MinIO
                if self._minio_client and parquet_bytes:
                    bucket = (
                        scan.destination.get("minio_bucket")
                        or self._minio_client._default_bucket
                    )
                    path = self._minio_client.upload_parquet_page(
                        data=parquet_bytes,
                        org_id=scan.org_id,
                        scan_id=scan.scan_id,
                        object_name=object_name,
                        page_number=page_count,
                        bucket=bucket,
                    )
                    if not minio_prefix:
                        minio_prefix = self._minio_client.get_object_prefix(
                            scan.org_id, scan.scan_id, object_name, bucket
                        )

                total_records += len(deduped)
                progress.pages_downloaded = page_count
                progress.records_processed = total_records
                scan.update_timestamp()

        except BulkAPIError as e:
            progress.mark_failed(f"Result download failed: {e}")
            return

        # Upload metadata
        if self._minio_client:
            try:
                self._minio_client.upload_metadata(
                    org_id=scan.org_id,
                    scan_id=scan.scan_id,
                    object_name=object_name,
                    total_records=total_records,
                    pages=page_count,
                    soql=soql,
                )
            except Exception as e:
                logger.warning(f"Metadata upload failed (non-critical): {e}")

        # Mark complete
        progress.mark_complete(
            records_processed=total_records,
            pages=page_count,
            minio_path=minio_prefix or "",
        )

        # Step 6: Cleanup Salesforce job
        self._bulk_client.delete_job(job_id)

    def _build_soql(self, object_name: str, filters: Optional[dict] = None) -> str:
        """
        Build a SOQL query for the given object.

        Uses the SOQL template and default fields from the supported objects config.
        Applies incremental filters if provided.
        """
        obj_config = self._supported_objects.get(object_name)
        if not obj_config:
            # Fallback: simple SELECT *-equivalent
            return f"SELECT Id FROM {object_name}"

        fields = ", ".join(obj_config.get("default_fields", ["Id"]))
        where_clause = ""

        if filters:
            conditions = []
            last_modified = filters.get("last_modified_after")
            if last_modified and obj_config.get("supports_incremental"):
                conditions.append(
                    f"LastModifiedDate >= {last_modified}"
                )

            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

        template = obj_config.get(
            "soql_template",
            f"SELECT {{fields}} FROM {object_name} {{where_clause}}"
        )
        return template.format(fields=fields, where_clause=where_clause).strip()

    def get_scan(self, scan_id: str) -> Optional[ScanState]:
        """Get a scan by ID."""
        with self._lock:
            return self._scans.get(scan_id)

    def list_scans(
        self,
        status: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> list[ScanState]:
        """List all scans, optionally filtered."""
        with self._lock:
            scans = list(self._scans.values())

        if status:
            scans = [s for s in scans if s.status.value == status]
        if org_id:
            scans = [s for s in scans if s.org_id == org_id]

        return scans

    def cancel_scan(self, scan_id: str) -> Optional[ScanState]:
        """Cancel an in-progress scan."""
        scan = self.get_scan(scan_id)
        if not scan:
            return None

        for progress in scan.progress.values():
            if progress.state not in ("JobComplete", "Failed", "Aborted"):
                progress.mark_aborted()
                # Attempt to abort the Salesforce job
                if progress.sf_job_id:
                    try:
                        self._bulk_client.abort_job(progress.sf_job_id)
                    except Exception as e:
                        logger.warning(
                            f"Failed to abort SF job {progress.sf_job_id}: {e}"
                        )

        scan.status = ScanStatus.CANCELLED
        scan.update_timestamp()
        return scan

    def remove_scan(self, scan_id: str) -> bool:
        """Remove a scan record."""
        with self._lock:
            if scan_id in self._scans:
                del self._scans[scan_id]
                return True
            return False
