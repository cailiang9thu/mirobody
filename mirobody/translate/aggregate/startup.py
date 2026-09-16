"""Registering the aggregation job with the shared scheduler."""

import logging

from mirobody.utils.scheduler import scheduler
from .task import AggregateIndicatorTask

logger = logging.getLogger(__name__)

_aggregate_task = None


async def start_aggregate_indicator_scheduler(run_integration_test: bool = False):
    """Register the aggregate indicator task.

    `run_integration_test` is accepted and ignored: it used to import
    `.test_aggregator`, a module that is not in the package (the tests live in
    the gitignored `tests/` root), so passing True raised ImportError. The only
    caller passes False.
    """
    global _aggregate_task

    if _aggregate_task is not None:
        logger.warning("Aggregate indicator task already registered")
        return

    _aggregate_task = AggregateIndicatorTask()
    scheduler.register_task(_aggregate_task)
    logger.info("Aggregate indicator task registered successfully")
