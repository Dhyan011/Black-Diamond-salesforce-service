"""
Salesforce Service — Normalization Service

CSV → Parquet conversion using pandas/pyarrow.

Converts raw CSV records from Salesforce Bulk API results
into columnar Parquet format for efficient downstream processing.
"""

import io
import logging
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


class NormalizationService:
    """
    Converts CSV records (list of dicts) to Parquet format.

    Uses pandas for DataFrame manipulation and pyarrow for Parquet writing.
    Handles Salesforce-specific data type conversions.
    """

    # Salesforce date/datetime field suffixes
    DATETIME_SUFFIXES = ("Date", "DateTime", "Timestamp", "date", "datetime")

    def __init__(self, compression: str = "snappy"):
        """
        Initialize the normalization service.

        Args:
            compression: Parquet compression codec ('snappy', 'gzip', 'zstd', 'none').
        """
        self._compression = compression

    def csv_records_to_parquet(
        self,
        records: list[dict],
        coerce_types: bool = True,
    ) -> bytes:
        """
        Convert a list of record dicts (from CSV parsing) to Parquet bytes.

        Args:
            records: List of dictionaries (one per record).
            coerce_types: If True, attempt to coerce date/datetime columns.

        Returns:
            Parquet file content as bytes.

        Raises:
            NormalizationError: If conversion fails.
        """
        if not records:
            logger.warning("No records to normalize — returning empty Parquet")
            return self._empty_parquet()

        try:
            # Create DataFrame from records
            df = pd.DataFrame(records)

            # Coerce data types
            if coerce_types:
                df = self._coerce_salesforce_types(df)

            # Convert to Parquet bytes
            return self._dataframe_to_parquet_bytes(df)

        except Exception as e:
            logger.error(f"Normalization failed: {e}")
            raise NormalizationError(f"CSV to Parquet conversion failed: {e}") from e

    def csv_text_to_parquet(self, csv_text: str) -> bytes:
        """
        Convert raw CSV text to Parquet bytes.

        Args:
            csv_text: Raw CSV string (with header row).

        Returns:
            Parquet file content as bytes.
        """
        if not csv_text or not csv_text.strip():
            return self._empty_parquet()

        try:
            df = pd.read_csv(io.StringIO(csv_text))
            df = self._coerce_salesforce_types(df)
            return self._dataframe_to_parquet_bytes(df)
        except Exception as e:
            logger.error(f"CSV text normalization failed: {e}")
            raise NormalizationError(f"CSV text conversion failed: {e}") from e

    def _coerce_salesforce_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Coerce Salesforce-specific data types.

        - Convert date/datetime columns to pandas datetime
        - Convert boolean string columns to bool
        - Leave other columns as strings
        """
        for col in df.columns:
            # Skip empty columns
            if df[col].isna().all():
                continue

            # Coerce datetime columns
            if any(col.endswith(suffix) for suffix in self.DATETIME_SUFFIXES):
                try:
                    df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
                except Exception:
                    pass  # Keep as string if conversion fails

            # Coerce boolean columns
            elif df[col].dtype == object:
                unique_vals = set(df[col].dropna().str.lower().unique())
                if unique_vals <= {"true", "false"}:
                    try:
                        df[col] = df[col].str.lower().map(
                            {"true": True, "false": False}
                        )
                    except Exception:
                        pass

        return df

    def _dataframe_to_parquet_bytes(self, df: pd.DataFrame) -> bytes:
        """Convert a pandas DataFrame to Parquet bytes."""
        buffer = io.BytesIO()

        # Convert to PyArrow Table
        table = pa.Table.from_pandas(df, preserve_index=False)

        # Write Parquet
        pq.write_table(
            table,
            buffer,
            compression=self._compression,
            use_dictionary=True,
            write_statistics=True,
        )

        parquet_bytes = buffer.getvalue()
        logger.debug(
            f"Normalized {len(df)} records to Parquet "
            f"({len(parquet_bytes)} bytes, {self._compression})"
        )
        return parquet_bytes

    def _empty_parquet(self) -> bytes:
        """Generate an empty Parquet file with a minimal schema."""
        schema = pa.schema([("_empty", pa.bool_())])
        table = pa.table({"_empty": []}, schema=schema)
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        return buffer.getvalue()

    def get_parquet_metadata(self, parquet_bytes: bytes) -> dict:
        """
        Read metadata from Parquet bytes.

        Returns:
            Dict with num_rows, num_columns, schema info.
        """
        try:
            buffer = io.BytesIO(parquet_bytes)
            pf = pq.ParquetFile(buffer)
            metadata = pf.metadata

            return {
                "num_rows": metadata.num_rows,
                "num_columns": metadata.num_columns,
                "num_row_groups": metadata.num_row_groups,
                "serialized_size": metadata.serialized_size,
                "columns": [
                    pf.schema_arrow.field(i).name
                    for i in range(metadata.num_columns)
                ],
            }
        except Exception as e:
            logger.error(f"Failed to read Parquet metadata: {e}")
            return {}


class NormalizationError(Exception):
    """Raised when data normalization (CSV → Parquet) fails."""
    pass
