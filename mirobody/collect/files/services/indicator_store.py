"""Readings extracted from a file, written to the series.

The write side of what ① Collect hands on: `save_indicators_to_db` takes what
the extractor found in one document and lands it through
`collect.readings.upsert_readings`, the single writer for `th_series_data`.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

from mirobody.collect.readings import upsert_readings

logger = logging.getLogger(__name__)


#: A value ending in letters, a percent sign or a slashed unit: the tail this
#: splits off. Anchored at a digit so "Negative" keeps its whole self.
#: MICRO SIGN and GREEK SMALL MU are different code points and both get
#: typed for micromoles; DEGREE CELSIUS is a third single-character unit.
_UNIT = "A-Za-z%\u00b0\u00b5\u03bc\u2103\u2109"
_TRAILING_UNIT = re.compile(rf"^(?P<value>.*\d\s*)(?P<unit>[{_UNIT}][{_UNIT}/0-9.^\-]*)$")


def split_unit(value: Any, unit: Any) -> tuple[str, str]:
    """`("5.2 %", "%")` -> `("5.2", "%")`: the number alone, and the unit.

    Extraction returns the unit twice, once in its own field and once still
    attached to the value, because that is how the page prints it. Stored as
    it arrived, `th_series_data.value` held "4.9 mmol/L", the Indicators table
    rendered "4.9 mmol/Lmmol/L", and nothing downstream could compare, average
    or chart the reading without parsing the string first. The device path has
    always written a bare number here.

    A value that is not a measurement is returned untouched: "Negative" keeps
    its whole self, and "120/80 mmHg" keeps "120/80".
    """
    text, declared = str(value or "").strip(), str(unit or "").strip()
    if declared and text.lower().endswith(declared.lower()):
        stripped = text[: -len(declared)].strip()
        if stripped and stripped[-1].isdigit():
            return stripped, declared
    match = _TRAILING_UNIT.match(text)
    if match:
        return match.group("value").strip(), declared or match.group("unit").strip()
    return text, declared


async def _save_to_series_data(db_params: list[dict[str, Any]]) -> int:
    """Parallel task: save to th_series_data table.

    The unique (user, indicator, start, end) key counts soft-deleted rows,
    and this used to be a bare ON CONFLICT DO NOTHING, so a report
    re-uploaded after its file was deleted wrote NOTHING (every reading
    collided with its own deleted copy) while the log said "Write
    complete: 9 records" and the file row said 9 indicators. A collision
    with a DELETED row now revives that row as the new reading; a
    collision with a live row is still left alone, that is what
    `on_conflict="revive_deleted"` means in `collect/readings.py`.
    """
    if not db_params:
        return 0

    return await upsert_readings(db_params, on_conflict="revive_deleted")

def generate_source_table_id(msg_id: str, file_key: str) -> str:
    """
    Generate source_table_id for th_series_data based on file_key.

    Uses file_key directly as source_table_id since source_table is th_files.
    file_key is the unique identifier in th_files table.

    Args:
        msg_id: Message ID (legacy parameter, kept for backward compatibility)
        file_key: File key from th_files table (primary identifier)

    Returns:
        str: file_key as source_table_id, or msg_id as fallback
    """
    # Use file_key directly as source_table_id
    if file_key:
        return file_key

    # Fallback to msg_id if no file_key (legacy support)
    return msg_id or ""

async def save_indicators_to_db(
    user_id: str,
    indicators: list[dict[str, Any]],
    start_time: datetime,
    date_source: str,
    msg_id: str,
    comment: str = "",
    source_table: str = "th_files",
    file_key: str = None,
) -> int:
    """Batch save health indicators to th_series_data table.

    `start_time` and `date_source` come from `resolve_report_date`; the
    caller resolves them so the same answer can be recorded on the file row.
    """
    try:
        end_time = start_time
        db_params = []

        for indicator in indicators:
            # Check required fields
            original_indicator = indicator.get("original_indicator")

            if not original_indicator:
                continue

            # Generate source_table_id with file-level precision
            source_table_id = generate_source_table_id(msg_id, file_key)

            value, unit = split_unit(indicator.get("value", ""), indicator.get("unit", ""))

            # Build comment JSON with unit, reference_range, detection_method
            # and the date's provenance (see resolve_report_date).
            try:
                comment_data = {
                    "unit": unit,
                    "reference_range": indicator.get("reference_range", ""),
                    "detection_method": indicator.get("detection_method", ""),
                    "date_source": date_source,
                }
                comment_json = json.dumps(comment_data, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Failed to build comment JSON for indicator {original_indicator}: {str(e)}")
                comment_json = ""

            # Build th_series_data parameters. `comment` is encrypted, so
            # the read side cannot select out of it: the unit goes in
            # `fhir_mapping_info` as well, the column every read path
            # actually reads it from (`fhir_mapping_info ->> 'unit'`), and
            # the one the device path has always written.
            db_params.append(
                {
                    "user_id": str(user_id),
                    "indicator": original_indicator,
                    "value": value,
                    "start_time": start_time,
                    "end_time": end_time,
                    "source_table": source_table,
                    "source_table_id": source_table_id,
                    "comment": comment_json,
                    "fhir_mapping_info": json.dumps({"unit": unit}),
                }
            )

        # Execute database write tasks
        if db_params:
            await _save_to_series_data(db_params)

        logger.info(f"Write complete: {len(db_params)} records, user_id: {user_id}")

        if db_params:
            # Signal the worker to materialize th_series_dim + backfill
            # embeddings (embedding_<UTILS_EMBEDDING_MODEL>), then refresh the
            # user profile. Both enqueues are coalescing + self-guarded, so a
            # Redis hiccup never fails the ingest write above.
            try:
                from mirobody.task import IndicatorSyncTask, ProfileRefreshTask
                await IndicatorSyncTask.enqueue("")
                await ProfileRefreshTask.enqueue(str(user_id))
            except Exception as e:
                logger.warning(f"Failed to enqueue indicator-sync/profile-refresh signals: {e}")

        return len(db_params)

    except Exception:
        logger.error(f"Failed to save indicators to database, user_id: {user_id}", stack_info=True)
        return 0
