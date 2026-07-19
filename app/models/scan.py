"""
Salesforce Service — Scan State Model

Tracks scan lifecycle: scan_id, org_id, status, per-object progress,
timestamps, and MinIO output paths.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class ScanStatus(str, enum.Enum):
    """Scan lifecycle states."""

    PENDING = "pending"
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_COMPLETED = "partially_completed"


@dataclass
class ObjectProgress:
    """Tracks extraction progress for a single Salesforce object within a scan."""

    object_name: str
    sf_job_id: Optional[str] = None
    state: str = "pending"
    records_processed: int = 0
    records_failed: int = 0
    pages_downloaded: int = 0
    minio_path: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "object_name": self.object_name,
            "sf_job_id": self.sf_job_id,
            "state": self.state,
            "records_processed": self.records_processed,
            "records_failed": self.records_failed,
            "pages_downloaded": self.pages_downloaded,
            "minio_path": self.minio_path,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def mark_started(self, sf_job_id: str) -> None:
        """Mark object extraction as started."""
        self.sf_job_id = sf_job_id
        self.state = "UploadComplete"
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_in_progress(self) -> None:
        """Mark as actively processing."""
        self.state = "InProgress"

    def mark_complete(self, records_processed: int, pages: int, minio_path: str) -> None:
        """Mark extraction as complete."""
        self.state = "JobComplete"
        self.records_processed = records_processed
        self.pages_downloaded = pages
        self.minio_path = minio_path
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_failed(self, error_message: str, records_failed: int = 0) -> None:
        """Mark extraction as failed."""
        self.state = "Failed"
        self.error_message = error_message
        self.records_failed = records_failed
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_aborted(self) -> None:
        """Mark extraction as aborted (cancelled)."""
        self.state = "Aborted"
        self.completed_at = datetime.now(timezone.utc).isoformat()


@dataclass
class ScanState:
    """
    Full scan state model.

    Tracks the lifecycle of a multi-object Salesforce extraction scan,
    including per-object progress and overall status.
    """

    scan_id: str
    org_id: str
    status: ScanStatus = ScanStatus.PENDING
    objects: list[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    output_format: str = "parquet"
    destination: dict = field(default_factory=dict)
    progress: dict[str, ObjectProgress] = field(default_factory=dict)
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        """Initialize progress tracking for each object."""
        if self.objects and not self.progress:
            for obj_name in self.objects:
                self.progress[obj_name] = ObjectProgress(object_name=obj_name)

    def start(self) -> None:
        """Transition scan to started state."""
        self.status = ScanStatus.STARTED
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.started_at

    def update_timestamp(self) -> None:
        """Update the last-modified timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def check_completion(self) -> None:
        """
        Check if all objects have finished and update scan status accordingly.
        Called after each object completes or fails.
        """
        self.update_timestamp()

        all_states = [p.state for p in self.progress.values()]
        terminal_states = {"JobComplete", "Failed", "Aborted"}

        if not all(s in terminal_states for s in all_states):
            self.status = ScanStatus.IN_PROGRESS
            return

        # All objects are terminal
        failed = sum(1 for s in all_states if s == "Failed")
        aborted = sum(1 for s in all_states if s == "Aborted")
        complete = sum(1 for s in all_states if s == "JobComplete")

        if aborted > 0:
            self.status = ScanStatus.CANCELLED
        elif failed > 0 and complete > 0:
            self.status = ScanStatus.PARTIALLY_COMPLETED
        elif failed > 0:
            self.status = ScanStatus.FAILED
        else:
            self.status = ScanStatus.COMPLETED

        self.completed_at = datetime.now(timezone.utc).isoformat()

    @property
    def totals(self) -> dict:
        """Compute aggregate totals across all objects."""
        return {
            "objects_total": len(self.progress),
            "objects_complete": sum(
                1 for p in self.progress.values() if p.state == "JobComplete"
            ),
            "objects_failed": sum(
                1 for p in self.progress.values() if p.state == "Failed"
            ),
            "records_extracted": sum(
                p.records_processed for p in self.progress.values()
            ),
        }

    def to_dict(self) -> dict:
        """Serialize scan state to dictionary (for API responses)."""
        return {
            "scan_id": self.scan_id,
            "org_id": self.org_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "objects": self.objects,
            "filters": self.filters,
            "output_format": self.output_format,
            "progress": {
                name: prog.to_dict() for name, prog in self.progress.items()
            },
            "totals": self.totals,
            "error_message": self.error_message,
        }

    @classmethod
    def create(
        cls,
        scan_id: str,
        org_id: str,
        objects: list[str],
        filters: Optional[dict] = None,
        output_format: str = "parquet",
        destination: Optional[dict] = None,
    ) -> ScanState:
        """Factory method to create a new scan."""
        scan = cls(
            scan_id=scan_id,
            org_id=org_id,
            objects=objects,
            filters=filters or {},
            output_format=output_format,
            destination=destination or {},
        )
        scan.start()
        return scan
