"""How much data a person has, for the Home data bar and the Files panel.

Two numbers: how many readings, across how many departments. The frontend
consumes only those, which is why `distribution` comes back empty rather than
carrying per-bucket rows nobody reads.
"""

import logging
from typing import Any

from mirobody.utils import execute_query

logger = logging.getLogger(__name__)


async def get_user_data_distribution(user_id: str) -> dict[str, Any]:
    """Return aggregate counts of the user's processed health data.

    Frontend (web Home DataBar / Drive "Clean data" panel) consumes only
    ``total_records`` and ``total_categories``. The per-bucket
    ``distribution`` list has no consumer and is returned empty.
    """
    try:
        user_id = str(user_id)

        logger.info(f"Getting user data distribution: user_id={user_id}")

        # Aggregate counts from th_series_data + th_series_data_genetic.
        # Categories are derived from th_series_dim.department, with 'Other'
        # for rows whose indicator has no department mapping and 'genetic'
        # if the user has any genetic records.
        query = """
        SELECT
            (
                SELECT COUNT(1) FROM th_series_data
                WHERE user_id = :user_id AND deleted = 0
            ) + (
                SELECT COUNT(1) FROM th_series_data_genetic
                WHERE user_id = :user_id AND is_deleted = false
            ) AS total_records,
            (
                SELECT COUNT(DISTINCT cat) FROM (
                    SELECT TRIM(d.dept) AS cat
                    FROM th_series_data t1
                    JOIN th_series_dim t2 ON t1.indicator = t2.original_indicator
                    CROSS JOIN LATERAL unnest(string_to_array(t2.department, ',')) AS d(dept)
                    WHERE t1.user_id = :user_id
                      AND t2.department IS NOT NULL
                      AND TRIM(t2.department) <> ''
                      AND TRIM(d.dept) <> ''
                    UNION
                    SELECT 'Other' WHERE EXISTS (
                        SELECT 1 FROM th_series_data t1
                        LEFT JOIN th_series_dim t2 ON t1.indicator = t2.original_indicator
                        WHERE t1.user_id = :user_id
                          AND (t2.department IS NULL OR TRIM(t2.department) = '')
                    )
                    UNION
                    SELECT 'genetic' WHERE EXISTS (
                        SELECT 1 FROM th_series_data_genetic WHERE user_id = :user_id
                    )
                ) cats
            ) AS total_categories
        """

        results = await execute_query(query=query, params={"user_id": user_id})

        if results:
            row = results[0] if isinstance(results[0], dict) else dict(results[0])
            total_records = row.get("total_records") or 0
            total_categories = row.get("total_categories") or 0
        else:
            total_records = 0
            total_categories = 0

        logger.info(
            f"Query completed: user={user_id}, total_categories={total_categories}, total_records={total_records}"
        )

        return {
            "user_id": user_id,
            "total_categories": total_categories,
            "total_records": total_records,
            "distribution": [],
        }

    except Exception as e:
        logger.error(f"Failed to query data distribution: {str(e)}", stack_info=True)
        raise Exception(f"Failed to query data distribution: {str(e)}")
