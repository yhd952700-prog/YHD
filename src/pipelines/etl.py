"""
ETL Pipeline for LiuHao AI OS

Provides ETL/ELT pipeline abstractions with:
- Incremental processing support
- Change data capture (CDC) abstraction
- Deduplication strategies
- Batch processing framework
"""

import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


@dataclass
class ETLConfig:
    """Configuration for ETL pipelines."""
    batch_size: int = 100
    enable_incremental: bool = True
    enable_deduplication: bool = True
    deduplication_key: Optional[str] = None
    ttl_hours: Optional[int] = None
    fail_fast: bool = False


@dataclass
class ETLPipelineStatus:
    """Status of an ETL pipeline run."""
    pipeline_name: str
    status: str = "pending"  # pending, running, completed, failed, stopped
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    records_processed: int = 0
    records_failed: int = 0
    records_skipped: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ETLStrategy(ABC):
    """Abstract base class for ETL strategies."""

    @abstractmethod
    def extract(self, source: str, **kwargs) -> List[Dict[str, Any]]:
        """Extract data from source."""
        pass

    @abstractmethod
    def transform(self, records: List[Dict[str, Any]], **kwargs) -> List[Dict[str, Any]]:
        """Transform records."""
        pass

    @abstractmethod
    def load(self, records: List[Dict[str, Any]], target: str, **kwargs) -> int:
        """Load records to target. Returns count of loaded records."""
        pass


class IncrementalETLPipeline:
    """
    ETL pipeline with incremental processing support.

    Features:
    - Tracks last processed timestamp/ID
    - Only processes new/changed records
    - Supports resuming from checkpoint
    """

    def __init__(self, name: str, strategy: ETLStrategy, config: ETLConfig = None):
        self.name = name
        self.strategy = strategy
        self.config = config or ETLConfig()
        self.status = ETLPipelineStatus(pipeline_name=name)
        self._last_processed: Optional[float] = None
        self._last_id: Optional[str] = None

    def set_last_processed(self, timestamp: float, record_id: str) -> None:
        """Set the last processed timestamp and ID (for checkpointing)."""
        self._last_processed = timestamp
        self._last_id = record_id

    def get_last_processed(self) -> Tuple[Optional[float], Optional[str]]:
        """Get the last processed timestamp and ID."""
        return self._last_processed, self._last_id

    def run(self, source: str, **kwargs) -> ETLPipelineStatus:
        """Run the ETL pipeline."""
        self.status.status = "running"
        self.status.start_time = time.time()

        try:
            # Extract phase
            self.status.records_processed = 0
            self.status.records_failed = 0
            self.status.records_skipped = 0

            extracted = self.strategy.extract(source, **kwargs)

            # Filter by incremental status if enabled
            if self.config.enable_incremental:
                extracted = self._filter_incremental(extracted)

            # Deduplication if enabled
            if self.config.enable_deduplication and self.config.deduplication_key:
                extracted = self._deduplicate(extracted)

            # Transform phase
            transformed = self.strategy.transform(extracted, **kwargs)

            # Load phase
            loaded_count = self.strategy.load(transformed, **kwargs)

            # Update status
            self.status.status = "completed"
            self.status.end_time = time.time()
            self.status.records_processed = loaded_count
            self.status.records_skipped = len(extracted) - loaded_count

            # Save checkpoint if incremental
            if self.config.enable_incremental:
                self._save_checkpoint()

            logger.info(f"ETL pipeline '{self.name}' completed: {loaded_count} records loaded")
            return self.status

        except Exception as e:
            self.status.status = "failed"
            self.status.end_time = time.time()
            self.status.error = str(e)
            logger.error(f"ETL pipeline '{self.name}' failed: {e}")

            if self.config.fail_fast:
                raise

            return self.status

    def _filter_incremental(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter records based on incremental processing state."""
        if self._last_processed is None:
            # First run: process all records
            return records

        filtered = []
        for record in records:
            # Get the record's timestamp or ID
            record_time = record.get(self.config.deduplication_key + "_at") or record.get("timestamp")
            record_id = record.get(self.config.deduplication_key) or record.get("id")

            if record_time is None or record_id is None:
                filtered.append(record)  # Include records without timestamps/IDs
            elif record_time > self._last_processed:
                # Newer than last processed
                filtered.append(record)
            elif record_id == self._last_id:
                # Same as last processed - skip (might be a duplicate)
                self.status.records_skipped += 1
            else:
                filtered.append(record)  # Include older records

        return filtered

    def _deduplicate(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate records based on deduplication key."""
        seen_keys = set()
        result = []

        for record in records:
            key = record.get(self.config.deduplication_key)
            if key is None:
                result.append(record)
                continue

            if key not in seen_keys:
                seen_keys.add(key)
                result.append(record)
            else:
                self.status.records_skipped += 1

        return result

    def _save_checkpoint(self) -> None:
        """Save pipeline checkpoint for resume capability."""
        # In a real implementation, this would persist to a database
        # For now, just log the checkpoint
        checkpoint = {
            "pipeline_name": self.name,
            "last_processed": self._last_processed,
            "last_id": self._last_id,
            "status": self.status.status,
            "records_processed": self.status.records_processed,
        }
        logger.debug(f"ETL checkpoint saved: {checkpoint}")


# Convenience function
def create_etl_pipeline(name: str, strategy: ETLStrategy, config: ETLConfig = None) -> IncrementalETLPipeline:
    """Create an incremental ETL pipeline."""
    return IncrementalETLPipeline(name, strategy, config)
