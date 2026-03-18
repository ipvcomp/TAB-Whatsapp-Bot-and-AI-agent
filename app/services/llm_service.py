import asyncio
import logging
from typing import Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

LLM_REQUEST_TIMEOUT = 120
LLM_RETRY_MAX_ATTEMPTS = 3
LLM_RETRY_BACKOFF = [2, 4, 8]


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

    for attempt in range(1, LLM_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=LLM_REQUEST_TIMEOUT) as client:
                logger.info(f"LLM generic request for user {user_id}, node={current_node} (attempt {attempt}/{LLM_RETRY_MAX_ATTEMPTS})")
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                try:
                    response_data = response.json()
                except Exception:
                    logger.warning(f"LLM generic returned non-JSON: HTTP {response.status_code} - {response.text[:500]} (attempt {attempt})")
                    if response.status_code in (502, 503, 504) and attempt < LLM_RETRY_MAX_ATTEMPTS:
                        await asyncio.sleep(LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)])
                        continue
                    return None

                if response.status_code == 200 and response_data.get("success"):
                    logger.info(f"LLM generic response received for user {user_id}")
                    return response_data

                if response.status_code in (429, 502, 503, 504) and attempt < LLM_RETRY_MAX_ATTEMPTS:
                    wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                    logger.warning(f"LLM generic HTTP {response.status_code} (attempt {attempt}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                logger.error(f"LLM generic error: HTTP {response.status_code} - {response_data}")
                return None

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < LLM_RETRY_MAX_ATTEMPTS:
                wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                logger.warning(f"LLM generic {type(e).__name__} (attempt {attempt}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"LLM generic failed after {LLM_RETRY_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")
        except Exception as e:
            if attempt < LLM_RETRY_MAX_ATTEMPTS:
                wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                logger.warning(f"LLM generic error (attempt {attempt}): {type(e).__name__}: {e}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"LLM generic request error after {LLM_RETRY_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")

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

    for attempt in range(1, LLM_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=LLM_REQUEST_TIMEOUT) as client:
                logger.info(f"LLM extract request: field={field_name}, user={user_id} (attempt {attempt}/{LLM_RETRY_MAX_ATTEMPTS})")
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                try:
                    response_data = response.json()
                except Exception:
                    logger.warning(f"LLM extract returned non-JSON: HTTP {response.status_code} - {response.text[:500]} (attempt {attempt})")
                    if response.status_code in (502, 503, 504) and attempt < LLM_RETRY_MAX_ATTEMPTS:
                        await asyncio.sleep(LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)])
                        continue
                    return None

                if response.status_code == 200 and response_data.get("success"):
                    logger.info(
                        f"LLM extract response: field={field_name}, "
                        f"valid={response_data.get('is_valid')}, "
                        f"extracted={response_data.get('extracted_value')}"
                    )
                    return response_data

                if response.status_code in (429, 502, 503, 504) and attempt < LLM_RETRY_MAX_ATTEMPTS:
                    wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                    logger.warning(f"LLM extract HTTP {response.status_code} (attempt {attempt}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                logger.error(f"LLM extract error: HTTP {response.status_code} - {response_data}")
                return None

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < LLM_RETRY_MAX_ATTEMPTS:
                wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                logger.warning(f"LLM extract {type(e).__name__} (attempt {attempt}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"LLM extract failed after {LLM_RETRY_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")
        except Exception as e:
            if attempt < LLM_RETRY_MAX_ATTEMPTS:
                wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                logger.warning(f"LLM extract error (attempt {attempt}): {type(e).__name__}: {e}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"LLM extract request error after {LLM_RETRY_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")

    return None
