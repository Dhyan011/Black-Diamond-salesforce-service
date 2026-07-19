"""
Salesforce Service — Polling Service

Async polling loop with adaptive intervals for Bulk API 2.0 jobs.
Implements the polling strategy from Section 3.2:

| Elapsed time  | Poll interval |
|---------------|---------------|
| 0 – 30s       | every 5s      |
| 30s – 5min    | every 15s     |
| 5min – 30min  | every 60s     |
| > 30min       | every 120s    |
"""

import time
import logging
from typing import Callable, Optional

from app.models.job import BulkJobResult

logger = logging.getLogger(__name__)


class PollingService:
    """
    Manages adaptive polling for Salesforce Bulk API query jobs.

    Provides both synchronous and callback-based polling patterns.
    """

    # Adaptive polling thresholds (elapsed_seconds, interval_seconds)
    POLL_SCHEDULE = [
        (30, 5),      # 0-30s: poll every 5s
        (300, 15),     # 30s-5min: poll every 15s
        (1800, 60),    # 5min-30min: poll every 60s
        (float("inf"), 120),  # >30min: poll every 120s
    ]

    def __init__(self, max_timeout_seconds: int = 21600):
        """
        Initialize the polling service.

        Args:
            max_timeout_seconds: Maximum polling duration (default: 6 hours).
        """
        self._max_timeout = max_timeout_seconds

    def get_poll_interval(self, elapsed_seconds: float) -> int:
        """
        Get the appropriate poll interval based on elapsed time.

        Args:
            elapsed_seconds: Seconds since polling started.

        Returns:
            Interval in seconds until next poll.
        """
        for threshold, interval in self.POLL_SCHEDULE:
            if elapsed_seconds < threshold:
                return interval
        return 120  # Fallback

    def poll_with_callback(
        self,
        check_fn: Callable[[], dict],
        is_terminal_fn: Callable[[dict], bool],
        on_progress_fn: Optional[Callable[[dict, float], None]] = None,
        job_id: str = "",
    ) -> dict:
        """
        Poll using a callback pattern.

        Args:
            check_fn: Function that checks job status (returns status dict).
            is_terminal_fn: Function that returns True if job is in terminal state.
            on_progress_fn: Optional callback for progress updates (status_dict, elapsed).
            job_id: Job ID for logging.

        Returns:
            Final status dict.

        Raises:
            PollingTimeoutError: If max timeout is exceeded.
        """
        start_time = time.time()
        poll_count = 0

        while True:
            elapsed = time.time() - start_time

            # Check timeout
            if elapsed > self._max_timeout:
                raise PollingTimeoutError(
                    f"Polling timed out after {elapsed:.0f}s "
                    f"(max: {self._max_timeout}s) for job {job_id}"
                )

            # Check status
            try:
                status = check_fn()
                poll_count += 1
            except Exception as e:
                logger.warning(
                    f"Poll check failed for job {job_id} "
                    f"(attempt {poll_count + 1}): {e}"
                )
                # Continue polling despite transient errors
                interval = self.get_poll_interval(elapsed)
                time.sleep(interval)
                continue

            # Check if terminal
            if is_terminal_fn(status):
                logger.info(
                    f"Job {job_id} reached terminal state after "
                    f"{elapsed:.0f}s ({poll_count} polls)"
                )
                return status

            # Progress callback
            if on_progress_fn:
                on_progress_fn(status, elapsed)

            # Wait for next poll
            interval = self.get_poll_interval(elapsed)
            logger.debug(
                f"Job {job_id}: elapsed={elapsed:.0f}s, "
                f"next poll in {interval}s (poll #{poll_count})"
            )
            time.sleep(interval)

    def estimate_completion_time(
        self,
        records_processed: int,
        total_records: int,
        elapsed_seconds: float,
    ) -> Optional[float]:
        """
        Estimate remaining time based on processing rate.

        Args:
            records_processed: Records processed so far.
            total_records: Total records expected.
            elapsed_seconds: Seconds elapsed.

        Returns:
            Estimated seconds remaining, or None if can't estimate.
        """
        if records_processed <= 0 or elapsed_seconds <= 0:
            return None

        rate = records_processed / elapsed_seconds  # records per second
        remaining_records = total_records - records_processed

        if rate <= 0:
            return None

        return remaining_records / rate


class PollingTimeoutError(Exception):
    """Raised when polling exceeds the maximum allowed duration."""
    pass
