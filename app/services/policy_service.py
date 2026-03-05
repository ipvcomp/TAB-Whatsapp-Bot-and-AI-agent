import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.database import get_database

logger = logging.getLogger(__name__)

COLLECTION = "policies"

STATUS_DRAFT = "draft"
STATUS_PRODUCT_SELECTED = "product_selected"
STATUS_PENDING = "pending"
STATUS_SUBMITTED = "submitted"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"


async def ensure_indexes():
    db = get_database()
    if db is None:
        return
    collection = db[COLLECTION]
    await collection.create_index("user_id")
    await collection.create_index("status")
    await collection.create_index("created_at")
    await collection.create_index("updated_at")
    await collection.create_index([("user_id", 1), ("status", 1)])
    await collection.create_index([("user_id", 1), ("created_at", -1)])
    logger.info("Policy indexes ensured")


async def create_policy(user_id: str, phone_number: str) -> Optional[dict]:
    db = get_database()
    if db is None:
        logger.error("Database not available for policy creation")
        return None

    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    doc = {
        "user_id": user_id,
        "phone_number": phone_number,
        "status": STATUS_DRAFT,
        "country_code": None,
        "country_name": None,
        "selected_product": None,
        "personal_details": None,
        "payment_method": None,
        "bank_details": None,
        "msisdn_info": None,
        "channel_info": None,
        "airport_info": None,
        "itinerary": None,
        "submitted_policy": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        result = await collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        policy_id = str(result.inserted_id)
        logger.info(f"Policy created: {policy_id} for user {user_id}")
        return {"policy_id": policy_id, **doc}
    except Exception as e:
        logger.error(f"Failed to create policy for {user_id}: {e}")
        return None


async def get_active_draft(user_id: str) -> Optional[dict]:
    db = get_database()
    if db is None:
        return None

    collection = db[COLLECTION]
    try:
        doc = await collection.find_one(
            {
                "user_id": user_id,
                "status": {"$in": [STATUS_DRAFT, STATUS_PRODUCT_SELECTED]},
            },
            sort=[("updated_at", -1)],
        )
        if doc:
            doc["policy_id"] = str(doc.pop("_id"))
            return doc
        return None
    except Exception as e:
        logger.error(f"Failed to get active draft for {user_id}: {e}")
        return None


async def update_policy(policy_id: str, updates: dict) -> bool:
    db = get_database()
    if db is None:
        return False

    from bson import ObjectId

    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    try:
        result = await collection.update_one(
            {"_id": ObjectId(policy_id)},
            {
                "$set": {
                    **updates,
                    "updated_at": now,
                }
            },
        )
        if result.modified_count > 0:
            logger.info(f"Policy {policy_id} updated: {list(updates.keys())}")
            return True
        else:
            logger.warning(f"Policy {policy_id} not found or no changes")
            return False
    except Exception as e:
        logger.error(f"Failed to update policy {policy_id}: {e}")
        return False


async def set_country(policy_id: str, country_code: str, country_name: str) -> bool:
    return await update_policy(policy_id, {
        "country_code": country_code,
        "country_name": country_name,
    })


async def set_product_selection(policy_id: str, product: dict) -> bool:
    return await update_policy(policy_id, {
        "selected_product": product,
        "status": STATUS_PRODUCT_SELECTED,
    })


async def set_personal_details(policy_id: str, details: dict) -> bool:
    return await update_policy(policy_id, {
        "personal_details": details,
    })


async def set_payment_method(policy_id: str, method: str) -> bool:
    return await update_policy(policy_id, {
        "payment_method": method,
        "status": STATUS_PENDING,
    })


async def cancel_policy(policy_id: str) -> bool:
    return await update_policy(policy_id, {
        "status": STATUS_CANCELLED,
    })


async def get_policy_by_id(policy_id: str) -> Optional[dict]:
    db = get_database()
    if db is None:
        return None

    from bson import ObjectId

    collection = db[COLLECTION]
    try:
        doc = await collection.find_one({"_id": ObjectId(policy_id)})
        if doc:
            doc["policy_id"] = str(doc.pop("_id"))
            return doc
        return None
    except Exception as e:
        logger.error(f"Failed to get policy {policy_id}: {e}")
        return None


async def get_user_policies(user_id: str, limit: int = 20) -> list:
    db = get_database()
    if db is None:
        return []

    collection = db[COLLECTION]
    try:
        cursor = collection.find(
            {"user_id": user_id},
            sort=[("created_at", -1)],
            limit=limit,
        )
        policies = []
        async for doc in cursor:
            doc["policy_id"] = str(doc.pop("_id"))
            policies.append(doc)
        return policies
    except Exception as e:
        logger.error(f"Failed to get policies for {user_id}: {e}")
        return []
