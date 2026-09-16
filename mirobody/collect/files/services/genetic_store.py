"""Deleting a genetic import, by the file it came from.

A genetic file lands as many rows in `th_series_data_genetic`, all carrying the
source file's identity. Re-processing or removing that file has to take its
rows with it, or the next import doubles the person's variants.
"""

import logging

from mirobody.utils import execute_query

logger = logging.getLogger(__name__)


async def delete_genetic_data_by_source(user_id: str, source_table: str, source_table_id: str) -> bool:
    """
    Delete genetic data by source table and ID

    Args:
        user_id: User ID
        source_table: Source table name
        source_table_id: Source table record ID

    Returns:
        bool: Whether deletion was successful
    """
    try:
        # Delete genetic data
        sql = """
            DELETE FROM th_series_data_genetic 
            WHERE user_id = :user_id 
            AND source_table = :source_table 
            AND source_table_id = :source_table_id
        """

        params = {
            "user_id": user_id,
            "source_table": source_table,
            "source_table_id": source_table_id,
        }

        await execute_query(query=sql, params=params,)

        logger.info(f"Genetic data deleted successfully, user_id: {user_id}, source_table: {source_table}, source_table_id: {source_table_id}")
        return True

    except Exception:
        logger.error(f"Failed to delete genetic data, user_id: {user_id}, source_table: {source_table}, source_table_id: {source_table_id}", stack_info=True)
        return False
