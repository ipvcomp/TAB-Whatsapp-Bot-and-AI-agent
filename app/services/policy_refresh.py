"""Background policy-cache refresh utilities.

This module provides a stale-while-revalidate helper that can be used by any
flow service.  When a caller has already served stale cached policies to the
user it calls ``schedule_policy_cache_refresh`` to kick off an asyncio task
that silently fetches fresh data and writes it back to the session.  The user
receives an immediate response from the stale cache while the refresh happens
in the background; the *next* interaction will find an up-to-date cache.
"""

import asyncio
import logging
from typing import Optional

from app.services.session_service import get_session, save_session, set_policy_cache
from app.services.ipurvey_api import fetch_policies_by_msisdn

logger = logging.getLogger(__name__)

_in_flight: set[str] = set()


async def _refresh_policy_cache(wa_id: str) -> None:
    """Fetch fresh policies from the Ipurvey API and write them to the session.

    Designed to run as a fire-and-forget asyncio task.  All exceptions are
    caught and logged so that a failed refresh never crashes the event loop.
    The ``_in_flight`` guard is always cleared on exit so future refreshes are
    not permanently blocked.
    """
    try:
        logger.info("Background policy refresh started for %s", wa_id[:4] + "****")
        policies = await fetch_policies_by_msisdn(wa_id)
        session = await get_session(wa_id)
        if session is None:
            logger.warning(
                "Background policy refresh: session not found for %s; skipping cache write",
                wa_id[:4] + "****",
            )
            return
        set_policy_cache(session, policies)
        await save_session(session)
        logger.info(
            "Background policy refresh complete – %d policies cached for %s",
            len(policies),
            wa_id[:4] + "****",
        )
    except Exception:
        logger.exception(
            "Background policy refresh failed for %s", wa_id[:4] + "****"
        )
    finally:
        _in_flight.discard(wa_id)


def schedule_policy_cache_refresh(wa_id: str) -> Optional[asyncio.Task]:
    """Schedule a background policy-cache refresh for *wa_id*.

    If a refresh is already in-flight for this user the call is a no-op, which
    prevents duplicate concurrent API calls under burst traffic.  Creates an
    asyncio Task on the running event loop and returns it.  Returns None (and
    logs a warning) when called outside of an async context.
    """
    if wa_id in _in_flight:
        logger.debug(
            "Background policy refresh already in-flight for %s; skipping duplicate",
            wa_id[:4] + "****",
        )
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "schedule_policy_cache_refresh called outside of async context for %s; refresh skipped",
            wa_id[:4] + "****",
        )
        return None

    _in_flight.add(wa_id)
    task = loop.create_task(_refresh_policy_cache(wa_id))
    task.add_done_callback(
        lambda t: logger.debug("Background policy refresh task finished for %s", wa_id[:4] + "****")
    )
    return task
