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
        "id_type": None,
        "nin": None,
        "bvn": None,
        "payment_method": None,
        "payout_method": None,
        "account_number": None,
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


async def set_id_verification(policy_id: str, id_type: str, id_number: str) -> bool:
    update_data = {"id_type": id_type}
    if id_type == "NIN":
        update_data["nin"] = id_number
        update_data["bvn"] = None
    elif id_type == "BVN":
        update_data["bvn"] = id_number
        update_data["nin"] = None
    return await update_policy(policy_id, update_data)


async def set_payment_method(policy_id: str, method: str) -> bool:
    return await update_policy(policy_id, {
        "payment_method": method,
        "status": STATUS_PENDING,
    })


async def set_payout_method(policy_id: str, method: str) -> bool:
    return await update_policy(policy_id, {
        "payout_method": method,
    })


async def set_account_number(policy_id: str, account_number: str) -> bool:
    return await update_policy(policy_id, {
        "account_number": account_number,
    })


async def set_bank_details(policy_id: str, bank_details: dict) -> bool:
    return await update_policy(policy_id, {
        "bank_details": bank_details,
    })


async def set_msisdn_info(policy_id: str, msisdn_info: dict) -> bool:
    return await update_policy(policy_id, {
        "msisdn_info": msisdn_info,
    })


async def set_channel_info(policy_id: str, channel_info: dict) -> bool:
    return await update_policy(policy_id, {
        "channel_info": channel_info,
    })


async def set_airport_info(policy_id: str, airport_info: dict) -> bool:
    return await update_policy(policy_id, {
        "airport_info": airport_info,
    })


async def set_itinerary(policy_id: str, itinerary: dict) -> bool:
    return await update_policy(policy_id, {
        "itinerary": itinerary,
    })


async def set_policy_submitted(policy_id: str, api_response: dict) -> bool:
    from datetime import datetime, timezone
    return await update_policy(policy_id, {
        "status": "submitted",
        "submitted_at": datetime.now(timezone.utc),
        "api_response": api_response,
    })


async def set_boarding_pass(policy_id: str, boarding_pass: dict) -> bool:
    from bson import Binary
    from datetime import datetime, timezone

    data = {
        "media_id": boarding_pass.get("media_id", ""),
        "mime_type": boarding_pass.get("mime_type", ""),
        "sha256": boarding_pass.get("sha256", ""),
        "caption": boarding_pass.get("caption"),
        "file_size": boarding_pass.get("file_size"),
        "uploaded_at": datetime.now(timezone.utc),
    }
    raw_bytes = boarding_pass.get("bytes")
    if raw_bytes:
        data["file_data"] = Binary(raw_bytes)

    return await update_policy(policy_id, {"boarding_pass": data})


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
