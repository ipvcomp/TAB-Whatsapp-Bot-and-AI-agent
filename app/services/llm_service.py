import logging
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def call_generic(
    user_id: str,
    phone_number: str,
    message: str,
    user_name: str,
    current_node: str,
) -> Optional[dict]:
    settings = get_settings()

    if not settings.LLM_API_URL:
        logger.error("LLM_API_URL is not configured")
        return None

    url = f"{settings.LLM_API_URL}/api/v1/generic"

    payload = {
        "user_id": user_id,
        "phone_number": phone_number,
        "message": message,
        "user_name": user_name,
        "current_node": current_node,
    }

    try:
        async with httpx.AsyncClient(timeout=float(settings.LLM_API_TIMEOUT)) as client:
            logger.info(f"LLM generic request for user {user_id}, node={current_node}")
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            try:
                response_data = response.json()
            except Exception:
                logger.error(f"LLM generic returned non-JSON: HTTP {response.status_code} - {response.text[:500]}")
                return None

            if response.status_code == 200 and response_data.get("success"):
                logger.info(f"LLM generic response received for user {user_id}")
                return response_data
            else:
                logger.error(f"LLM generic error: HTTP {response.status_code} - {response_data}")
                return None
    except httpx.TimeoutException:
        logger.error(f"LLM generic timeout after {settings.LLM_API_TIMEOUT}s for user {user_id}")
        return None
    except httpx.ConnectError:
        logger.error(f"LLM generic connection failed: {url}")
        return None
    except Exception as e:
        logger.error(f"LLM generic request error: {type(e).__name__}: {e}")
        return None


async def call_extract(
    user_id: str,
    field_name: str,
    question_asked: str,
    user_response: str,
    expected_format: str = "text",
) -> Optional[dict]:
    settings = get_settings()

    if not settings.LLM_API_URL:
        logger.error("LLM_API_URL is not configured")
        return None

    url = f"{settings.LLM_API_URL}/api/v1/extract"

    payload = {
        "user_id": user_id,
        "field_name": field_name,
        "question_asked": question_asked,
        "user_response": user_response,
        "expected_format": expected_format,
    }

    try:
        async with httpx.AsyncClient(timeout=float(settings.LLM_API_TIMEOUT)) as client:
            logger.info(f"LLM extract request: field={field_name}, user={user_id}")
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            try:
                response_data = response.json()
            except Exception:
                logger.error(f"LLM extract returned non-JSON: HTTP {response.status_code} - {response.text[:500]}")
                return None

            if response.status_code == 200 and response_data.get("success"):
                logger.info(
                    f"LLM extract response: field={field_name}, "
                    f"valid={response_data.get('is_valid')}, "
                    f"extracted={response_data.get('extracted_value')}"
                )
                return response_data
            else:
                logger.error(f"LLM extract error: HTTP {response.status_code} - {response_data}")
                return None
    except httpx.TimeoutException:
        logger.error(f"LLM extract timeout after {settings.LLM_API_TIMEOUT}s for field {field_name}")
        return None
    except httpx.ConnectError:
        logger.error(f"LLM extract connection failed: {url}")
        return None
    except Exception as e:
        logger.error(f"LLM extract request error: {type(e).__name__}: {e}")
        return None
