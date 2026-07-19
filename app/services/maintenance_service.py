"""
Salesforce Service — Maintenance Service

Cleanup old scan records and MinIO objects based on CLEANUP_DAYS.
Runs as a scheduled or on-demand maintenance task via POST /api/maintenance/cleanup.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class MaintenanceService:
    """
    Manages cleanup of old scan records and associated MinIO data.

    Responsibilities:
    - Remove scan records older than the configured retention period
    - Delete associated MinIO objects for cleaned-up scans
    - Provide statistics on storage usage
    """

    def __init__(
        self,
        cleanup_days: int = 30,
        minio_client=None,
    ):
        """
        Initialize the maintenance service.

        Args:
            cleanup_days: Remove scan records older than this many days.
            minio_client: MinIO client for storage cleanup (optional).
        """
        self._cleanup_days = cleanup_days
        self._minio_client = minio_client

    def cleanup_old_scans(
        self,
        scans: dict,
        dry_run: bool = False,
    ) -> dict:
        """
        Remove scan records older than the retention period.

        Args:
            scans: Dictionary of scan_id -> scan_data (mutable, will be modified).
            dry_run: If True, report what would be cleaned without actually removing.

        Returns:
            Cleanup report with removed scan IDs and statistics.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self._cleanup_days)

        candidates = []
        removed = []
        errors = []

        for scan_id, scan_data in list(scans.items()):
            started_at = self._parse_timestamp(scan_data)
            if started_at is None:
                continue

            if started_at < cutoff:
                candidates.append(scan_id)

        for scan_id in candidates:
            if dry_run:
                removed.append(scan_id)
                continue

            try:
                scan_data = scans[scan_id]

                # Clean up MinIO data if client is available
                if self._minio_client:
                    self._cleanup_minio_data(scan_data)

                # Remove the scan record
                del scans[scan_id]
                removed.append(scan_id)
                logger.info(f"Cleaned up scan: {scan_id}")

            except Exception as e:
                logger.error(f"Failed to clean up scan {scan_id}: {e}")
                errors.append({"scan_id": scan_id, "error": str(e)})

        report = {
            "success": True,
            "dry_run": dry_run,
            "cleanup_threshold_days": self._cleanup_days,
            "cutoff_date": cutoff.isoformat(),
            "scans_evaluated": len(scans) + len(removed),
            "scans_removed": len(removed),
            "removed_scan_ids": removed,
            "errors": errors,
        }

        logger.info(
            f"Cleanup {'(dry run) ' if dry_run else ''}"
            f"completed: {len(removed)} scans removed, "
            f"{len(errors)} errors"
        )

        return report

    def _cleanup_minio_data(self, scan_data: dict) -> None:
        """Delete MinIO objects associated with a scan."""
        if not self._minio_client:
            return

        org_id = scan_data.get("org_id", "")
        scan_id = scan_data.get("scan_id", "")

        if not org_id or not scan_id:
            return

        prefix = f"{org_id}/{scan_id}/"
        try:
            deleted = self._minio_client.delete_prefix(prefix)
            logger.info(
                f"Deleted {deleted} MinIO objects for scan {scan_id}"
            )
        except Exception as e:
            logger.warning(
                f"MinIO cleanup failed for scan {scan_id}: {e}"
            )

    def _parse_timestamp(self, scan_data: dict) -> Optional[datetime]:
        """Parse the started_at timestamp from scan data."""
        # Handle both dict and object-like scan data
        started_at = None
        if isinstance(scan_data, dict):
            started_at = scan_data.get("started_at")
        elif hasattr(scan_data, "started_at"):
            started_at = scan_data.started_at

        if not started_at:
            return None

        try:
            if isinstance(started_at, str):
                return datetime.fromisoformat(started_at)
            return started_at
        except (ValueError, TypeError):
            return None

    def get_storage_stats(self, scans: dict) -> dict:
        """
        Calculate storage statistics across all scans.

        Returns:
            Dict with total scans, records, and status distribution.
        """
        total_scans = len(scans)
        total_records = 0
        status_counts: dict[str, int] = {}
        oldest_scan = None
        newest_scan = None

        for scan_data in scans.values():
            # Count by status
            status = (
                scan_data.get("status", "unknown")
                if isinstance(scan_data, dict)
                else getattr(scan_data, "status", "unknown")
            )
            if hasattr(status, "value"):
                status = status.value
            status_counts[status] = status_counts.get(status, 0) + 1

            # Count records
            progress = (
                scan_data.get("progress", {})
                if isinstance(scan_data, dict)
                else getattr(scan_data, "progress", {})
            )
            for obj_progress in progress.values():
                if isinstance(obj_progress, dict):
                    total_records += obj_progress.get("records_processed", 0)
                else:
                    total_records += getattr(obj_progress, "records_processed", 0)

            # Track oldest/newest
            ts = self._parse_timestamp(scan_data)
            if ts:
                if oldest_scan is None or ts < oldest_scan:
                    oldest_scan = ts
                if newest_scan is None or ts > newest_scan:
                    newest_scan = ts

        return {
            "total_scans": total_scans,
            "total_records_extracted": total_records,
            "status_distribution": status_counts,
            "oldest_scan": oldest_scan.isoformat() if oldest_scan else None,
            "newest_scan": newest_scan.isoformat() if newest_scan else None,
            "retention_days": self._cleanup_days,
        }
