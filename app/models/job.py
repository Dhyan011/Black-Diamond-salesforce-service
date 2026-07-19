"""
Salesforce Service — Bulk API Job Model

Represents a Salesforce Bulk API 2.0 query job and its state transitions.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class BulkJobState(str, enum.Enum):
    """Salesforce Bulk API 2.0 job states."""

    UPLOAD_COMPLETE = "UploadComplete"
    IN_PROGRESS = "InProgress"
    JOB_COMPLETE = "JobComplete"
    FAILED = "Failed"
    ABORTED = "Aborted"

    @property
    def is_terminal(self) -> bool:
        """Check if this is a terminal (final) state."""
        return self in (
            BulkJobState.JOB_COMPLETE,
            BulkJobState.FAILED,
            BulkJobState.ABORTED,
        )

    @property
    def is_success(self) -> bool:
        """Check if this represents a successful completion."""
        return self == BulkJobState.JOB_COMPLETE

    @property
    def is_error(self) -> bool:
        """Check if this represents an error state."""
        return self == BulkJobState.FAILED


@dataclass
class BulkJobResult:
    """
    Result of a completed Salesforce Bulk API 2.0 query job.

    Populated after polling detects a terminal state.
    """

    job_id: str
    state: str
    records_processed: int = 0
    records_failed: int = 0
    error_message: Optional[str] = None
    total_processing_time: Optional[int] = None
    api_active_processing_time: Optional[int] = None

    @property
    def is_success(self) -> bool:
        """Check if the job completed successfully."""
        return self.state == BulkJobState.JOB_COMPLETE.value

    @property
    def is_failed(self) -> bool:
        """Check if the job failed."""
        return self.state == BulkJobState.FAILED.value

    @property
    def is_aborted(self) -> bool:
        """Check if the job was aborted."""
        return self.state == BulkJobState.ABORTED.value

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "job_id": self.job_id,
            "state": self.state,
            "records_processed": self.records_processed,
            "records_failed": self.records_failed,
            "error_message": self.error_message,
            "total_processing_time": self.total_processing_time,
            "api_active_processing_time": self.api_active_processing_time,
        }

    @classmethod
    def from_api_response(cls, data: dict) -> BulkJobResult:
        """Create a BulkJobResult from a Salesforce API response."""
        return cls(
            job_id=data.get("id", ""),
            state=data.get("state", ""),
            records_processed=data.get("numberRecordsProcessed", 0),
            records_failed=data.get("numberRecordsFailed", 0),
            error_message=data.get("errorMessage"),
            total_processing_time=data.get("totalProcessingTime"),
            api_active_processing_time=data.get("apiActiveProcessingTime"),
        )


@dataclass
class BulkJobConfig:
    """Configuration for creating a Salesforce Bulk API query job."""

    object_name: str
    soql: str
    content_type: str = "CSV"
    column_delimiter: str = "COMMA"
    line_ending: str = "LF"

    def to_api_payload(self) -> dict:
        """Convert to Salesforce Bulk API 2.0 request payload."""
        return {
            "operation": "query",
            "query": self.soql,
            "contentType": self.content_type,
            "columnDelimiter": self.column_delimiter,
            "lineEnding": self.line_ending,
        }
