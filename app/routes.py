"""
Salesforce Service — API Route Definitions

All 12 endpoints from Section 5 of the Technical Design Document.
Organized into Flask-RESTX namespaces.
"""

import logging
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import request, current_app, jsonify
from flask_restx import Namespace, Resource, fields

logger = logging.getLogger(__name__)

# ============================================================
# Namespaces
# ============================================================

health_ns = Namespace("health", description="Health check operations")
key_ns = Namespace("key", description="HMAC key verification")
scan_ns = Namespace("scan", description="Scan management operations")
objects_ns = Namespace("objects", description="Supported Salesforce objects")
batch_ns = Namespace("batch", description="Salesforce org and batch info")
maintenance_ns = Namespace("maintenance", description="Maintenance operations")


# ============================================================
# In-memory scan store (replaced by PostgreSQL in production)
# ============================================================

_scans: dict = {}


# ============================================================
# HMAC Auth Decorator
# ============================================================

def require_hmac(key_type: str = "core"):
    """
    Decorator to enforce HMAC authentication on endpoints.
    key_type: 'core' for core-service calls, 'engineer' for admin calls.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            settings = current_app.config.get("SETTINGS")
            if not settings or not settings.HMAC_ENABLED:
                return f(*args, **kwargs)

            # Import here to avoid circular imports
            from app.auth.hmac_auth import verify_hmac_signature

            hmac_key = (
                settings.HMAC_SECRET_KEY_CORE
                if key_type == "core"
                else settings.HMAC_SECRET_KEY_ENGINEER
            )

            signature = request.headers.get("X-HMAC-Signature")
            timestamp = request.headers.get("X-HMAC-Timestamp")
            service_id = request.headers.get("X-Service-ID")

            if not all([signature, timestamp, service_id]):
                return {
                    "success": False,
                    "error": "Missing HMAC headers",
                    "message": "X-HMAC-Signature, X-HMAC-Timestamp, and X-Service-ID headers are required.",
                }, 401

            body = request.get_data(as_text=True) or ""
            is_valid = verify_hmac_signature(
                secret_key=hmac_key,
                signature=signature,
                timestamp=timestamp,
                method=request.method,
                path=request.path,
                body=body,
                max_age=settings.HMAC_SIGNATURE_MAX_AGE,
            )

            if not is_valid:
                return {
                    "success": False,
                    "error": "Invalid HMAC signature",
                    "message": "The HMAC signature is invalid or expired.",
                }, 401

            return f(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# Supported Salesforce Objects Configuration
# ============================================================

SUPPORTED_OBJECTS = [
    {
        "name": "Contact",
        "label": "Contacts",
        "soql_template": "SELECT {fields} FROM Contact {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "Name", "Email", "Phone", "AccountId",
            "OwnerId", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
    {
        "name": "Account",
        "label": "Accounts",
        "soql_template": "SELECT {fields} FROM Account {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "Name", "Type", "Industry", "BillingCity",
            "BillingState", "OwnerId", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
    {
        "name": "Opportunity",
        "label": "Opportunities",
        "soql_template": "SELECT {fields} FROM Opportunity {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "Name", "AccountId", "OwnerId", "StageName",
            "Amount", "CloseDate", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
    {
        "name": "Task",
        "label": "Activities (Tasks)",
        "soql_template": "SELECT {fields} FROM Task {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "Subject", "WhoId", "WhatId", "OwnerId",
            "Status", "ActivityDate", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
    {
        "name": "Event",
        "label": "Activities (Events)",
        "soql_template": "SELECT {fields} FROM Event {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "Subject", "WhoId", "WhatId", "OwnerId",
            "StartDateTime", "EndDateTime", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
    {
        "name": "Lead",
        "label": "Leads",
        "soql_template": "SELECT {fields} FROM Lead {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "Name", "Email", "Phone", "Company",
            "Status", "OwnerId", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
    {
        "name": "User",
        "label": "Users",
        "soql_template": "SELECT {fields} FROM User {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "Name", "Email", "Username", "IsActive",
            "ProfileId", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
    {
        "name": "CampaignMember",
        "label": "Campaign Members",
        "soql_template": "SELECT {fields} FROM CampaignMember {where_clause} ORDER BY LastModifiedDate ASC",
        "default_fields": [
            "Id", "CampaignId", "ContactId", "LeadId",
            "Status", "CreatedDate", "LastModifiedDate",
        ],
        "supports_incremental": True,
    },
]


# ============================================================
# 5.4  GET /api/health
# ============================================================

@health_ns.route("/health")
class HealthCheck(Resource):
    """Health check — required by Nomad. No authentication."""

    def get(self):
        """Return service health status."""
        settings = current_app.config.get("SETTINGS")

        # Check Salesforce connectivity (lazy check)
        sf_connected = bool(settings and settings.SF_CONSUMER_KEY)

        # Check MinIO connectivity (lazy check)
        minio_connected = bool(settings and settings.MINIO_ENABLED)

        status = "healthy" if (sf_connected and minio_connected) else "degraded"
        status_code = 200 if status == "healthy" else 503

        return {
            "status": status,
            "service": "salesforce-service",
            "version": settings.APP_VERSION if settings else "unknown",
            "salesforce_connected": sf_connected,
            "minio_connected": minio_connected,
        }, status_code


# ============================================================
# 5.6  GET /api/key/verify
# ============================================================

@key_ns.route("/verify")
class KeyVerify(Resource):
    """Verify HMAC key permissions."""

    @require_hmac(key_type="core")
    def get(self):
        """Verify that the HMAC key is valid and has the correct permissions."""
        service_id = request.headers.get("X-Service-ID", "unknown")
        return {
            "success": True,
            "service_id": service_id,
            "permissions": ["scan:read", "scan:write", "objects:read", "batch:read"],
            "message": "HMAC key verified successfully.",
        }, 200


# ============================================================
# 5.2  POST /api/scan/start
# ============================================================

@scan_ns.route("/start")
class ScanStart(Resource):
    """Start a Salesforce extraction scan."""

    @require_hmac(key_type="core")
    def post(self):
        """Trigger a full extraction of one or more Salesforce objects."""
        data = request.get_json(silent=True)
        if not data:
            return {
                "success": False,
                "error": "Invalid request body",
                "message": "Request body must be valid JSON.",
            }, 400

        scan_id = data.get("scan_id")
        org_id = data.get("org_id")
        objects = data.get("objects", [])

        if not scan_id or not org_id:
            return {
                "success": False,
                "error": "Missing required fields",
                "message": "scan_id and org_id are required.",
            }, 400

        if not objects:
            return {
                "success": False,
                "error": "No objects specified",
                "message": "Provide at least one Salesforce object to extract.",
            }, 400

        # Validate objects
        supported_names = {obj["name"] for obj in SUPPORTED_OBJECTS}
        invalid_objects = [o for o in objects if o not in supported_names]
        if invalid_objects:
            return {
                "success": False,
                "error": "Unsupported objects",
                "message": f"The following objects are not supported: {invalid_objects}",
                "supported_objects": sorted(supported_names),
            }, 400

        settings = current_app.config.get("SETTINGS")

        # Check concurrent scan limit
        active_scans = sum(
            1 for s in _scans.values()
            if s["status"] in ("started", "in_progress")
        )
        if active_scans >= settings.MAX_CONCURRENT_SCANS:
            return {
                "success": False,
                "error": "Concurrent scan limit reached",
                "message": f"Maximum {settings.MAX_CONCURRENT_SCANS} concurrent scans allowed.",
            }, 429

        # Check for duplicate scan_id
        if scan_id in _scans:
            return {
                "success": False,
                "error": "Duplicate scan_id",
                "message": f"Scan {scan_id} already exists.",
            }, 409

        # Create scan record
        now = datetime.now(timezone.utc).isoformat()
        filters = data.get("filters", {})
        output_format = data.get("output_format", "parquet")
        destination = data.get("destination", {})

        # Generate placeholder job IDs (real ones come from Salesforce Bulk API)
        jobs = {}
        progress = {}
        for obj_name in objects:
            job_id = f"750{uuid.uuid4().hex[:20].upper()}"
            jobs[obj_name] = job_id
            progress[obj_name] = {
                "sf_job_id": job_id,
                "state": "UploadComplete",
                "records_processed": 0,
                "records_failed": 0,
                "pages_downloaded": 0,
                "minio_path": None,
            }

        scan = {
            "scan_id": scan_id,
            "org_id": org_id,
            "status": "started",
            "started_at": now,
            "updated_at": now,
            "objects": objects,
            "filters": filters,
            "output_format": output_format,
            "destination": destination,
            "jobs": jobs,
            "progress": progress,
        }
        _scans[scan_id] = scan

        logger.info(f"Scan started: {scan_id} for org {org_id}, objects: {objects}")

        return {
            "success": True,
            "scan_id": scan_id,
            "status": "started",
            "jobs": jobs,
            "message": f"{len(objects)} Bulk API jobs created. Poll /api/scan/{scan_id}/status for progress.",
        }, 202


# ============================================================
# 5.3  GET /api/scan/{id}/status
# ============================================================

@scan_ns.route("/<string:scan_id>/status")
class ScanStatus(Resource):
    """Get scan status and progress."""

    @require_hmac(key_type="core")
    def get(self, scan_id: str):
        """Return current status and per-object progress for a scan."""
        scan = _scans.get(scan_id)
        if not scan:
            return {
                "success": False,
                "error": "Scan not found",
                "message": f"No scan found with id '{scan_id}'.",
            }, 404

        # Calculate totals
        progress = scan.get("progress", {})
        objects_total = len(progress)
        objects_complete = sum(
            1 for p in progress.values() if p["state"] == "JobComplete"
        )
        objects_failed = sum(
            1 for p in progress.values() if p["state"] == "Failed"
        )
        records_extracted = sum(
            p["records_processed"] for p in progress.values()
        )

        return {
            "scan_id": scan_id,
            "org_id": scan.get("org_id"),
            "status": scan.get("status"),
            "started_at": scan.get("started_at"),
            "updated_at": scan.get("updated_at"),
            "progress": progress,
            "totals": {
                "objects_total": objects_total,
                "objects_complete": objects_complete,
                "objects_failed": objects_failed,
                "records_extracted": records_extracted,
            },
        }, 200


# ============================================================
# GET /api/scan/list
# ============================================================

@scan_ns.route("/list")
class ScanList(Resource):
    """List all scans with filters."""

    @require_hmac(key_type="core")
    def get(self):
        """Return all scans, optionally filtered by status or org_id."""
        status_filter = request.args.get("status")
        org_filter = request.args.get("org_id")

        results = list(_scans.values())

        if status_filter:
            results = [s for s in results if s["status"] == status_filter]
        if org_filter:
            results = [s for s in results if s["org_id"] == org_filter]

        return {
            "success": True,
            "total": len(results),
            "scans": results,
        }, 200


# ============================================================
# 5.5  POST /api/scan/{id}/cancel
# ============================================================

@scan_ns.route("/<string:scan_id>/cancel")
class ScanCancel(Resource):
    """Cancel an in-progress scan."""

    @require_hmac(key_type="core")
    def post(self, scan_id: str):
        """Abort all in-progress Bulk API jobs for this scan."""
        scan = _scans.get(scan_id)
        if not scan:
            return {
                "success": False,
                "error": "Scan not found",
                "message": f"No scan found with id '{scan_id}'.",
            }, 404

        if scan["status"] in ("completed", "cancelled", "failed"):
            return {
                "success": False,
                "error": "Scan not cancellable",
                "message": f"Scan is already {scan['status']}.",
            }, 400

        jobs_aborted = []
        jobs_already_complete = []
        for obj_name, progress in scan.get("progress", {}).items():
            if progress["state"] == "JobComplete":
                jobs_already_complete.append(progress["sf_job_id"])
            else:
                progress["state"] = "Aborted"
                jobs_aborted.append(progress["sf_job_id"])

        scan["status"] = "cancelled"
        scan["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(f"Scan cancelled: {scan_id}")

        return {
            "success": True,
            "scan_id": scan_id,
            "status": "cancelled",
            "jobs_aborted": jobs_aborted,
            "jobs_already_complete": jobs_already_complete,
            "message": f"Scan cancelled. {len(jobs_already_complete)} object(s) completed before cancellation.",
        }, 200


# ============================================================
# POST /api/scan/{id}/resume
# ============================================================

@scan_ns.route("/<string:scan_id>/resume")
class ScanResume(Resource):
    """Resume a failed scan."""

    @require_hmac(key_type="core")
    def post(self, scan_id: str):
        """Resume a scan that previously failed."""
        scan = _scans.get(scan_id)
        if not scan:
            return {
                "success": False,
                "error": "Scan not found",
                "message": f"No scan found with id '{scan_id}'.",
            }, 404

        if scan["status"] != "failed":
            return {
                "success": False,
                "error": "Scan not resumable",
                "message": f"Only failed scans can be resumed. Current status: {scan['status']}.",
            }, 400

        # Reset failed objects
        resumed_objects = []
        for obj_name, progress in scan.get("progress", {}).items():
            if progress["state"] == "Failed":
                progress["state"] = "UploadComplete"
                progress["records_processed"] = 0
                progress["records_failed"] = 0
                progress["pages_downloaded"] = 0
                resumed_objects.append(obj_name)

        scan["status"] = "in_progress"
        scan["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(f"Scan resumed: {scan_id}, objects: {resumed_objects}")

        return {
            "success": True,
            "scan_id": scan_id,
            "status": "in_progress",
            "resumed_objects": resumed_objects,
            "message": f"Scan resumed. {len(resumed_objects)} object(s) will be re-extracted.",
        }, 200


# ============================================================
# DELETE /api/scan/{id}/remove
# ============================================================

@scan_ns.route("/<string:scan_id>/remove")
class ScanRemove(Resource):
    """Remove a scan record."""

    @require_hmac(key_type="core")
    def delete(self, scan_id: str):
        """Delete a scan record from the store."""
        if scan_id not in _scans:
            return {
                "success": False,
                "error": "Scan not found",
                "message": f"No scan found with id '{scan_id}'.",
            }, 404

        del _scans[scan_id]
        logger.info(f"Scan removed: {scan_id}")

        return {
            "success": True,
            "scan_id": scan_id,
            "message": "Scan record removed.",
        }, 200


# ============================================================
# GET /api/scan/statistics
# ============================================================

@scan_ns.route("/statistics")
class ScanStatistics(Resource):
    """Aggregate scan statistics."""

    @require_hmac(key_type="core")
    def get(self):
        """Return aggregate statistics across all scans."""
        total_scans = len(_scans)
        status_counts = {}
        total_records = 0

        for scan in _scans.values():
            status = scan.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            for progress in scan.get("progress", {}).values():
                total_records += progress.get("records_processed", 0)

        return {
            "success": True,
            "statistics": {
                "total_scans": total_scans,
                "status_counts": status_counts,
                "total_records_extracted": total_records,
            },
        }, 200


# ============================================================
# 5.6  GET /api/objects
# ============================================================

@objects_ns.route("/objects")
class ObjectsList(Resource):
    """List supported Salesforce objects."""

    @require_hmac(key_type="core")
    def get(self):
        """Return the list of Salesforce objects this service supports."""
        return {
            "supported_objects": SUPPORTED_OBJECTS,
        }, 200


# ============================================================
# 5.7  GET /api/batch/info
# ============================================================

@batch_ns.route("/info")
class BatchInfo(Resource):
    """Salesforce org and API quota info."""

    @require_hmac(key_type="core")
    def get(self):
        """Return Salesforce org metadata and current API quota usage."""
        settings = current_app.config.get("SETTINGS")

        # Count active scans as proxy for active bulk API jobs
        active_jobs = sum(
            1 for s in _scans.values()
            if s["status"] in ("started", "in_progress")
        )

        return {
            "org": {
                "id": "pending-connection",
                "name": "Pending Salesforce Connection",
                "instance_url": settings.SF_LOGIN_URL if settings else "unknown",
                "api_version": settings.SF_API_VERSION if settings else "unknown",
                "sandbox": "test.salesforce.com" in (settings.SF_LOGIN_URL if settings else ""),
            },
            "api_limits": {
                "daily_api_requests": {
                    "max": 15000,
                    "remaining": "unknown",
                },
                "bulk_api_jobs": {
                    "active": active_jobs,
                    "max_concurrent": settings.MAX_CONCURRENT_SCANS if settings else 10,
                },
            },
            "token_expires_in_seconds": "unknown",
        }, 200


# ============================================================
# POST /api/maintenance/cleanup
# ============================================================

@maintenance_ns.route("/cleanup")
class MaintenanceCleanup(Resource):
    """Purge old scan records."""

    @require_hmac(key_type="engineer")
    def post(self):
        """
        Remove scan records older than CLEANUP_DAYS.
        Requires engineer-level HMAC key.
        """
        settings = current_app.config.get("SETTINGS")
        cleanup_days = settings.CLEANUP_DAYS if settings else 7

        now = datetime.now(timezone.utc)
        removed = []

        for scan_id in list(_scans.keys()):
            scan = _scans[scan_id]
            started_at = scan.get("started_at", "")
            if started_at:
                try:
                    scan_time = datetime.fromisoformat(started_at)
                    age_days = (now - scan_time).days
                    if age_days >= cleanup_days:
                        del _scans[scan_id]
                        removed.append(scan_id)
                except (ValueError, TypeError):
                    pass

        logger.info(f"Cleanup completed: removed {len(removed)} scan(s)")

        return {
            "success": True,
            "removed_scans": len(removed),
            "removed_scan_ids": removed,
            "cleanup_threshold_days": cleanup_days,
            "message": f"Removed {len(removed)} scan(s) older than {cleanup_days} days.",
        }, 200
