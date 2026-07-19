"""
Salesforce Service — Deduplication Service

Removes duplicate records by Salesforce Id within a page or scan.
Salesforce Bulk API results can contain duplicates when records
are modified during extraction.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DeduplicationService:
    """
    Deduplicates Salesforce records by their Id field.

    Strategy:
    - Within a page: keep the last occurrence (most recent modification)
    - Across pages: track seen Ids to detect cross-page duplicates
    """

    DEFAULT_ID_FIELD = "Id"

    def __init__(self, id_field: str = None):
        """
        Initialize the deduplication service.

        Args:
            id_field: Field name to use as unique identifier (default: 'Id').
        """
        self._id_field = id_field or self.DEFAULT_ID_FIELD
        self._seen_ids: set[str] = set()

    def deduplicate(
        self,
        records: list[dict],
        track_across_pages: bool = True,
    ) -> list[dict]:
        """
        Remove duplicate records from a list.

        Within the list, keeps the last occurrence of each Id
        (Salesforce returns records ordered by LastModifiedDate ASC,
        so the last occurrence is the most recent version).

        Args:
            records: List of record dicts from CSV parsing.
            track_across_pages: If True, also deduplicate against
                records seen in previous pages.

        Returns:
            Deduplicated list of records.
        """
        if not records:
            return []

        id_field = self._id_field

        # Check if the Id field exists
        if records and id_field not in records[0]:
            logger.warning(
                f"Id field '{id_field}' not found in records. "
                f"Available fields: {list(records[0].keys())}. "
                f"Skipping deduplication."
            )
            return records

        # Deduplicate within page (keep last occurrence)
        seen_in_page: dict[str, int] = {}
        for idx, record in enumerate(records):
            record_id = record.get(id_field)
            if record_id:
                seen_in_page[record_id] = idx

        # Build deduplicated list preserving order
        unique_indices = set(seen_in_page.values())
        deduped = []

        for idx, record in enumerate(records):
            if idx not in unique_indices:
                continue

            record_id = record.get(id_field)
            if not record_id:
                deduped.append(record)
                continue

            # Cross-page deduplication
            if track_across_pages:
                if record_id in self._seen_ids:
                    continue
                self._seen_ids.add(record_id)

            deduped.append(record)

        removed = len(records) - len(deduped)
        if removed > 0:
            logger.info(
                f"Deduplication: {len(records)} → {len(deduped)} "
                f"({removed} duplicates removed)"
            )

        return deduped

    def reset(self) -> None:
        """Reset the cross-page deduplication state."""
        count = len(self._seen_ids)
        self._seen_ids.clear()
        logger.debug(f"Deduplication state reset ({count} tracked Ids cleared)")

    @property
    def tracked_count(self) -> int:
        """Number of unique Ids tracked for cross-page deduplication."""
        return len(self._seen_ids)

    @staticmethod
    def deduplicate_simple(
        records: list[dict],
        id_field: str = "Id",
    ) -> list[dict]:
        """
        Stateless deduplication — no cross-page tracking.

        Convenience method for one-off deduplication.

        Args:
            records: List of record dicts.
            id_field: Field name for unique identifier.

        Returns:
            Deduplicated list (keeps last occurrence).
        """
        if not records:
            return []

        seen: dict[str, dict] = {}
        for record in records:
            record_id = record.get(id_field)
            if record_id:
                seen[record_id] = record
            else:
                # Records without an Id are always kept
                seen[id(record)] = record

        return list(seen.values())
