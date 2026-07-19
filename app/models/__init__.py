"""
Salesforce Service — Models Package

Data models for scan state tracking and Salesforce job management.
"""

from app.models.scan import ScanState, ScanStatus, ObjectProgress
from app.models.job import BulkJobResult, BulkJobState

__all__ = [
    "ScanState",
    "ScanStatus",
    "ObjectProgress",
    "BulkJobResult",
    "BulkJobState",
]
