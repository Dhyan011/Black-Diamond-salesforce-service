"""
Salesforce Service — MinIO Storage Client

S3-compatible object storage client for uploading Parquet files
and metadata to MinIO. Follows the layout from Section 10.2:

    salesforce-{env}/
      {org_id}/
        {scan_id}/
          contact/
            page_001.parquet
            page_002.parquet
            _metadata.json
          account/
            page_001.parquet
            _metadata.json
"""

import io
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


class MinIOClient:
    """
    MinIO (S3-compatible) upload helper for Salesforce extraction results.

    Handles:
    - Bucket creation and validation
    - Parquet page uploads
    - Metadata JSON uploads
    - Object listing and cleanup
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = False,
        bucket: str = "salesforce-dev",
    ):
        """
        Initialize the MinIO client.

        Args:
            endpoint: MinIO endpoint (host:port).
            access_key: MinIO access key.
            secret_key: MinIO secret key.
            secure: Use TLS for MinIO connection.
            bucket: Default bucket name.
        """
        self._client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._default_bucket = bucket
        self._ensure_bucket(bucket)

    def _ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it doesn't exist."""
        try:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)
                logger.info(f"Created MinIO bucket: {bucket}")
            else:
                logger.debug(f"MinIO bucket exists: {bucket}")
        except S3Error as e:
            logger.error(f"Failed to ensure bucket '{bucket}': {e}")
            raise

    def _build_object_path(
        self,
        org_id: str,
        scan_id: str,
        object_name: str,
        filename: str,
    ) -> str:
        """
        Build the MinIO object path following the layout convention.

        Returns:
            Path like: {org_id}/{scan_id}/{object_name_lower}/{filename}
        """
        return f"{org_id}/{scan_id}/{object_name.lower()}/{filename}"

    def upload_parquet_page(
        self,
        data: bytes,
        org_id: str,
        scan_id: str,
        object_name: str,
        page_number: int,
        bucket: Optional[str] = None,
    ) -> str:
        """
        Upload a Parquet page to MinIO.

        Args:
            data: Parquet file bytes.
            org_id: Organization identifier.
            scan_id: Scan identifier.
            object_name: Salesforce object name (e.g., 'Contact').
            page_number: Page number (1-indexed).
            bucket: Override default bucket.

        Returns:
            Full MinIO path (s3://bucket/path).
        """
        bucket = bucket or self._default_bucket
        filename = f"page_{page_number:03d}.parquet"
        object_path = self._build_object_path(
            org_id, scan_id, object_name, filename
        )

        data_stream = io.BytesIO(data)
        data_length = len(data)

        try:
            self._client.put_object(
                bucket_name=bucket,
                object_name=object_path,
                data=data_stream,
                length=data_length,
                content_type="application/octet-stream",
            )
            full_path = f"s3://{bucket}/{object_path}"
            logger.info(
                f"Uploaded {filename} ({data_length} bytes) to {full_path}"
            )
            return full_path
        except S3Error as e:
            logger.error(f"Failed to upload {object_path}: {e}")
            raise MinIOUploadError(f"Upload failed: {e}") from e

    def upload_metadata(
        self,
        org_id: str,
        scan_id: str,
        object_name: str,
        total_records: int,
        pages: int,
        soql: str,
        bucket: Optional[str] = None,
    ) -> str:
        """
        Upload a _metadata.json file for a completed object extraction.

        Args:
            org_id: Organization identifier.
            scan_id: Scan identifier.
            object_name: Salesforce object name.
            total_records: Total records extracted.
            pages: Number of Parquet pages.
            soql: SOQL query used for extraction.
            bucket: Override default bucket.

        Returns:
            Full MinIO path.
        """
        bucket = bucket or self._default_bucket
        object_path = self._build_object_path(
            org_id, scan_id, object_name, "_metadata.json"
        )

        metadata = {
            "object": object_name,
            "scan_id": scan_id,
            "org_id": org_id,
            "total_records": total_records,
            "pages": pages,
            "soql": soql,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "format": "parquet",
        }

        data = json.dumps(metadata, indent=2).encode("utf-8")
        data_stream = io.BytesIO(data)

        try:
            self._client.put_object(
                bucket_name=bucket,
                object_name=object_path,
                data=data_stream,
                length=len(data),
                content_type="application/json",
            )
            full_path = f"s3://{bucket}/{object_path}"
            logger.info(f"Uploaded metadata to {full_path}")
            return full_path
        except S3Error as e:
            logger.error(f"Failed to upload metadata {object_path}: {e}")
            raise MinIOUploadError(f"Metadata upload failed: {e}") from e

    def get_object_prefix(
        self,
        org_id: str,
        scan_id: str,
        object_name: str,
        bucket: Optional[str] = None,
    ) -> str:
        """Get the MinIO path prefix for a scan's object data."""
        bucket = bucket or self._default_bucket
        prefix = f"{org_id}/{scan_id}/{object_name.lower()}/"
        return f"s3://{bucket}/{prefix}"

    def list_objects(
        self,
        prefix: str,
        bucket: Optional[str] = None,
    ) -> list[str]:
        """List all objects under a given prefix."""
        bucket = bucket or self._default_bucket
        try:
            objects = self._client.list_objects(
                bucket_name=bucket,
                prefix=prefix,
                recursive=True,
            )
            return [obj.object_name for obj in objects]
        except S3Error as e:
            logger.error(f"Failed to list objects with prefix '{prefix}': {e}")
            return []

    def delete_prefix(
        self,
        prefix: str,
        bucket: Optional[str] = None,
    ) -> int:
        """
        Delete all objects under a given prefix.

        Args:
            prefix: Object path prefix to delete.
            bucket: Override default bucket.

        Returns:
            Number of objects deleted.
        """
        bucket = bucket or self._default_bucket
        try:
            objects = self._client.list_objects(
                bucket_name=bucket,
                prefix=prefix,
                recursive=True,
            )
            count = 0
            for obj in objects:
                self._client.remove_object(bucket, obj.object_name)
                count += 1

            logger.info(f"Deleted {count} objects under prefix '{prefix}'")
            return count
        except S3Error as e:
            logger.error(f"Failed to delete prefix '{prefix}': {e}")
            return 0

    def is_healthy(self) -> bool:
        """Check if MinIO is reachable."""
        try:
            self._client.bucket_exists(self._default_bucket)
            return True
        except Exception:
            return False


class MinIOUploadError(Exception):
    """Raised when a MinIO upload operation fails."""
    pass
