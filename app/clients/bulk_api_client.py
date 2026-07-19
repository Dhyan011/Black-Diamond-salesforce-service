"""
Salesforce Service — Bulk API 2.0 Client

Full wrapper for Salesforce Bulk API 2.0 query operations.
Implements the lifecycle from Section 3.2 of the Technical Design Document:

1. CREATE JOB      POST   /services/data/v59.0/jobs/query
2. POLL STATUS     GET    /services/data/v59.0/jobs/query/{jobId}
3. PAGINATE/GET    GET    /services/data/v59.0/jobs/query/{jobId}/results
4. CLOSE JOB       DELETE /services/data/v59.0/jobs/query/{jobId}

Includes:
- Adaptive polling (5s → 15s → 60s → 120s)
- Paginated CSV result download with Sforce-Locator
- Exponential backoff on rate limits (429)
- Job timeout enforcement
"""

import csv
import io
import time
import logging
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

from app.models.job import BulkJobResult, BulkJobConfig

logger = logging.getLogger(__name__)

# Salesforce API version and base path
API_VERSION = "v59.0"
JOBS_BASE = f"/services/data/{API_VERSION}/jobs/query"


class SalesforceBulkAPIClient:
    """
    Wrapper for Salesforce Bulk API 2.0 (query operations only).
    Handles job creation, polling, paginated result download, and cleanup.
    """

    # Adaptive polling intervals (seconds) per Section 3.2
    # 0-30s: every 5s, 30s-5min: every 15s, 5min-30min: every 60s, >30min: every 120s
    POLL_INTERVALS = [
        5, 5, 5, 5, 5, 5,          # 0-30s (6 × 5s)
        15, 15, 15, 15, 15, 15,     # 30s-2.5min (6 × 15s)
        15, 15, 15, 15, 15, 15,     # 2.5min-5min (6 × 15s)
        60, 60, 60, 60, 60,         # 5min-10min (5 × 60s)
        60, 60, 60, 60, 60,         # 10min-15min (5 × 60s)
        60, 60, 60, 60, 60,         # 15min-20min (5 × 60s)
        60, 60, 60, 60, 60,         # 20min-25min (5 × 60s)
        60, 60, 60, 60, 60,         # 25min-30min (5 × 60s)
    ]
    # After exhausting the list, default to 120s

    # Max retries for transient HTTP errors
    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 2  # seconds

    def __init__(self, token_manager, timeout: int = 30, max_job_timeout_hours: int = 6):
        """
        Initialize the Bulk API client.

        Args:
            token_manager: SalesforceTokenManager instance for auth.
            timeout: HTTP request timeout in seconds.
            max_job_timeout_hours: Abort jobs that exceed this duration.
        """
        self._tokens = token_manager
        self._timeout = timeout
        self._max_job_timeout = max_job_timeout_hours * 3600  # Convert to seconds

    def _headers(self, content_type: str = "application/json") -> dict:
        """Build authorization headers using the token manager."""
        token, _ = self._tokens.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _base_url(self) -> str:
        """Get the Salesforce instance URL."""
        _, instance_url = self._tokens.get_token()
        return instance_url

    def _request_with_retry(
        self,
        method: str,
        url: str,
        retries: int = None,
        **kwargs,
    ) -> requests.Response:
        """
        Make an HTTP request with retry logic for transient failures.

        Handles:
        - 429 Too Many Requests (rate limit) → exponential backoff
        - 5xx Server Errors → retry with backoff
        - Connection errors → retry with backoff
        """
        if retries is None:
            retries = self.MAX_RETRIES

        last_exception = None
        for attempt in range(retries + 1):
            try:
                resp = requests.request(method, url, **kwargs)

                # Rate limit — wait and retry
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(
                        f"Rate limited (429). Waiting {retry_after}s "
                        f"(attempt {attempt + 1}/{retries + 1})"
                    )
                    time.sleep(retry_after)
                    continue

                # Server error — retry with backoff
                if resp.status_code >= 500:
                    wait = self.RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        f"Server error ({resp.status_code}). "
                        f"Retrying in {wait}s (attempt {attempt + 1}/{retries + 1})"
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                wait = self.RETRY_BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    f"Connection error: {e}. "
                    f"Retrying in {wait}s (attempt {attempt + 1}/{retries + 1})"
                )
                time.sleep(wait)

        raise BulkAPIError(
            f"Request failed after {retries + 1} attempts: {last_exception}"
        )

    # ------------------------------------------------------------------
    # Job Lifecycle — Step 1: Create Query Job
    # ------------------------------------------------------------------

    def create_query_job(self, soql: str) -> str:
        """
        Creates a Bulk API 2.0 query job.

        Args:
            soql: SOQL query string.

        Returns:
            job_id: The Salesforce job ID.

        Raises:
            BulkAPIError: If job creation fails.
        """
        url = f"{self._base_url()}{JOBS_BASE}"
        payload = {
            "operation": "query",
            "query": soql,
            "contentType": "CSV",
            "columnDelimiter": "COMMA",
            "lineEnding": "LF",
        }

        try:
            resp = self._request_with_retry(
                "POST",
                url,
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
            job_id = resp.json()["id"]
            logger.info(f"Created Bulk API query job: {job_id}")
            return job_id
        except Exception as e:
            logger.error(f"Failed to create Bulk API job: {e}")
            raise BulkAPIError(f"Job creation failed: {e}") from e

    def create_query_job_from_config(self, config: BulkJobConfig) -> str:
        """Create a query job from a BulkJobConfig object."""
        return self.create_query_job(config.soql)

    # ------------------------------------------------------------------
    # Job Lifecycle — Step 2: Poll Until Complete
    # ------------------------------------------------------------------

    def poll_until_complete(self, job_id: str) -> BulkJobResult:
        """
        Blocks until the job reaches a terminal state.
        Uses adaptive polling intervals.

        Args:
            job_id: Salesforce Bulk API job ID.

        Returns:
            BulkJobResult with final state.

        Raises:
            BulkAPIError: If the job times out.
        """
        url = f"{self._base_url()}{JOBS_BASE}/{job_id}"
        intervals = iter(self.POLL_INTERVALS)
        start_time = time.time()

        while True:
            # Check job timeout
            elapsed = time.time() - start_time
            if elapsed > self._max_job_timeout:
                logger.error(
                    f"Job {job_id} timed out after {elapsed:.0f}s "
                    f"(max: {self._max_job_timeout}s)"
                )
                # Attempt to abort the job
                try:
                    self.abort_job(job_id)
                except Exception:
                    pass
                raise BulkAPIError(
                    f"Job {job_id} timed out after {elapsed:.0f}s"
                )

            try:
                resp = self._request_with_retry(
                    "GET",
                    url,
                    headers=self._headers(content_type=None),
                    timeout=self._timeout,
                )
                data = resp.json()
                state = data.get("state", "")

                if state in ("JobComplete", "Failed", "Aborted"):
                    result = BulkJobResult.from_api_response(data)
                    logger.info(
                        f"Job {job_id} finished: state={state}, "
                        f"records={result.records_processed}"
                    )
                    return result

                delay = next(intervals, 120)
                logger.debug(
                    f"Job {job_id} state={state}, "
                    f"elapsed={elapsed:.0f}s, next poll in {delay}s"
                )
                time.sleep(delay)

            except BulkAPIError:
                raise
            except Exception as e:
                logger.error(f"Error polling job {job_id}: {e}")
                raise BulkAPIError(f"Polling failed for job {job_id}: {e}") from e

    # ------------------------------------------------------------------
    # Job Lifecycle — Step 3: Paginate Results
    # ------------------------------------------------------------------

    def iter_results(
        self,
        job_id: str,
        page_size: int = 50000,
    ) -> Iterator[list[dict]]:
        """
        Yields pages of records (each page is a list of dicts).
        Handles Sforce-Locator pagination transparently.

        Args:
            job_id: Salesforce Bulk API job ID.
            page_size: Max records per page (default: 50000).

        Yields:
            List of record dicts per page.
        """
        locator = None
        page_num = 0

        while True:
            url = f"{self._base_url()}{JOBS_BASE}/{job_id}/results?maxRecords={page_size}"
            if locator:
                url += f"&locator={locator}"

            try:
                resp = self._request_with_retry(
                    "GET",
                    url,
                    headers=self._headers(content_type=None),
                    timeout=self._timeout * 6,  # Longer timeout for large result sets
                )

                # Parse CSV body
                text = resp.text
                reader = csv.DictReader(io.StringIO(text))
                records = list(reader)
                page_num += 1

                logger.info(
                    f"Job {job_id} page {page_num}: {len(records)} records"
                )
                yield records

                # Check for next page via Sforce-Locator header
                locator = resp.headers.get("Sforce-Locator")
                if not locator or locator == "null":
                    logger.info(
                        f"Job {job_id}: all {page_num} pages downloaded"
                    )
                    break

            except BulkAPIError:
                raise
            except Exception as e:
                logger.error(
                    f"Error downloading results for job {job_id}, "
                    f"page {page_num + 1}: {e}"
                )
                raise BulkAPIError(
                    f"Result download failed for job {job_id}: {e}"
                ) from e

    # ------------------------------------------------------------------
    # Job Lifecycle — Step 4: Cleanup
    # ------------------------------------------------------------------

    def delete_job(self, job_id: str) -> None:
        """
        Deletes a completed job from Salesforce (best-effort cleanup).

        Salesforce retains job results for 7 days. Explicit deletion
        is good practice but not mandatory.
        """
        url = f"{self._base_url()}{JOBS_BASE}/{job_id}"
        try:
            resp = requests.delete(
                url,
                headers=self._headers(content_type=None),
                timeout=self._timeout,
            )
            resp.raise_for_status()
            logger.info(f"Deleted Bulk API job: {job_id}")
        except Exception as e:
            logger.warning(f"Failed to delete job {job_id} (non-critical): {e}")

    def abort_job(self, job_id: str) -> None:
        """
        Abort an in-progress job by sending a PATCH with state=Aborted.
        """
        url = f"{self._base_url()}{JOBS_BASE}/{job_id}"
        try:
            resp = requests.patch(
                url,
                json={"state": "Aborted"},
                headers=self._headers(),
                timeout=self._timeout,
            )
            resp.raise_for_status()
            logger.info(f"Aborted Bulk API job: {job_id}")
        except Exception as e:
            logger.warning(f"Failed to abort job {job_id}: {e}")

    # ------------------------------------------------------------------
    # Utility: Get Job Status
    # ------------------------------------------------------------------

    def get_job_status(self, job_id: str) -> dict:
        """Get current status of a Bulk API job without polling."""
        url = f"{self._base_url()}{JOBS_BASE}/{job_id}"
        try:
            resp = self._request_with_retry(
                "GET",
                url,
                headers=self._headers(content_type=None),
                timeout=self._timeout,
            )
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to get status for job {job_id}: {e}")
            raise BulkAPIError(f"Status check failed for job {job_id}: {e}") from e


class BulkAPIError(Exception):
    """Raised when a Salesforce Bulk API operation fails."""
    pass
