import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.core.database import get_database

logger = logging.getLogger(__name__)

COLLECTION = "sessions"

DEFAULT_SESSION_NODE = "N01"

POLICY_CACHE_TTL_SECONDS = 300


async def ensure_indexes():
    db = get_database()
    if db is None:
        return
    collection = db[COLLECTION]
    await collection.create_index("user_id", unique=True)
    await collection.create_index("updated_at")
    logger.info("Session indexes ensured")


def build_default_session(
    user_id: str,
    phone_number: str,
    first_name: str = "",
) -> dict:
    return {
        "user_id": user_id,
        "phone_number": f"+{phone_number}" if not phone_number.startswith("+") else phone_number,
        "current_node": DEFAULT_SESSION_NODE,
        "last_node": None,
        "first_name": first_name,
        "tags": [],
        "active_trip_id": None,
        "active_policy_id": None,
        "active_policy_code": None,
        "active_claim_id": None,
        "temp_data": {},
        "last_intent": None,
    }


async def update_api_data(wa_id: str, updates: dict) -> None:
    session = await get_session(wa_id) or {"user_id": wa_id, "temp_data": {}}
    session.setdefault("api_data", {}).update(updates)
    await save_session(session)


async def get_session(user_id: str) -> Optional[dict]:
    db = get_database()
    if db is None:
        return None

    collection = db[COLLECTION]
    doc = await collection.find_one({"user_id": user_id})

    if doc:
        doc.pop("_id", None)
        doc.pop("created_at", None)
        doc.pop("updated_at", None)
        return doc

    return None


async def save_session(session: dict) -> Optional[dict]:
    db = get_database()
    if db is None:
        logger.error("Database not available for session save")
        return None

    user_id = session.get("user_id")
    if not user_id:
        logger.error("Cannot save session without user_id")
        return None

    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    try:
        await collection.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    **session,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )
        logger.info(f"Session saved for user {user_id}, node: {session.get('current_node')}")
        return session
    except Exception as e:
        logger.error(f"Failed to save session for {user_id}: {e}")
        return None


def get_policy_cache(session: dict) -> Optional[list]:
    """Return cached policies from the session if still within TTL.

    Returns None on cache miss, expiry, or malformed entry.
    Stale or malformed entries are removed from the session so they do not
    accumulate in the stored document.
    """
    cache = session.get("policy_cache")
    if not cache:
        return None
    cached_at_str = cache.get("cached_at")
    if not cached_at_str:
        session.pop("policy_cache", None)
        return None
    try:
        cached_at = datetime.fromisoformat(cached_at_str)
        if datetime.now(timezone.utc) - cached_at < timedelta(seconds=POLICY_CACHE_TTL_SECONDS):
            logger.debug("Policy cache hit (age: %s)", datetime.now(timezone.utc) - cached_at)
            return cache.get("policies")
    except Exception:
        pass
    session.pop("policy_cache", None)
    logger.debug("Policy cache expired or malformed; pruned from session")
    return None


def set_policy_cache(session: dict, policies: list) -> None:
    """Store policies in the session cache with the current timestamp."""
    session["policy_cache"] = {
        "policies": policies,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.debug("Policy cache set (%d policies)", len(policies))


def invalidate_policy_cache(session: dict) -> None:
    """Remove the policy cache from the session so the next request fetches fresh data."""
    if "policy_cache" in session:
        del session["policy_cache"]
        logger.debug("Policy cache invalidated")
