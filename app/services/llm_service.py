import asyncio
import logging
import re
from typing import Any, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

LLM_REQUEST_TIMEOUT = 120
LLM_RETRY_MAX_ATTEMPTS = 3
LLM_RETRY_BACKOFF = [2, 4, 8]


def get_llm_guidance(llm_result: Optional[dict]) -> Optional[str]:
    """Return the answer/clarification text from a wrapper result, if any.

    `call_extract` / `call_policy_flow_validate` map the route actions
    `answer` and `clarify` into `guidance_message`. When the LLM answered a
    user question (or asked for clarification) instead of extracting a value,
    step handlers must surface this text to the user and then re-show the
    original step prompt — otherwise the bot looks like it ignored the user.

    Returns None when a value was extracted (`is_valid`) or when there is no
    usable guidance text, so callers can fall back to their existing
    invalid-input handling unchanged.
    """
    if not llm_result or llm_result.get("is_valid"):
        return None
    guidance = llm_result.get("guidance_message")
    if guidance is None:
        return None
    guidance = str(guidance).strip()
    return guidance or None


def _map_expected_format_to_type(fmt: str) -> str:
    """Map legacy string formats to the strict enum allowed by /v1/route"""
    fmt_lower = str(fmt).lower()
    if "pnr" in fmt_lower or "booking" in fmt_lower:
        return "pnr"
    if "date" in fmt_lower:
        return "date"
    if "time" in fmt_lower:
        return "time"
    if "email" in fmt_lower:
        return "email"
    if "phone" in fmt_lower:
        return "phone"
    if "airport" in fmt_lower:
        return "airport"
    if "country" in fmt_lower:
        return "country"
    if "bvn" in fmt_lower or "nin" in fmt_lower or "id" in fmt_lower:
        return "bvn"
    if "name" in fmt_lower:
        return "name"
    if "digit" in fmt_lower or "number" in fmt_lower:
        return "number"
    if "enum" in fmt_lower:
        return "enum"
    return "free"


async def call_route(
    user_id: str,
    phone_number: str,
    message: str,
    user_name: str,
    current_node: str,
    expected_input: Optional[dict] = None,
    history: Optional[list] = None,
    session_data: Optional[dict] = None,
) -> Optional[dict]:
    """Single orchestrator endpoint for all LLM routing, extraction, and answering.

    This is the only HTTP LLM endpoint used by the bot. Legacy wrapper helpers
    below (`call_generic`, `call_extract`, `call_policy_flow_validate`) all
    dispatch through this function so the app always calls `/api/v1/route`.
    """
    settings = get_settings()

    if not settings.LLM_API_URL:
        logger.error("LLM_API_URL is not configured")
        return None

    url = f"{settings.LLM_API_URL}/api/v1/route"

    payload = {
        "user_id": user_id,
        "phone_number": phone_number,
        "message": message,
        "user_name": user_name,
        "context": {
            "current_node": current_node,
            "expected_input": expected_input,
            "history": history or [],
            "session_data": session_data or {}
        },
        "request_id": f"req-{user_id}-{current_node}"
    }

    for attempt in range(1, LLM_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=LLM_REQUEST_TIMEOUT) as client:
                logger.info(f"LLM route request for user {user_id}, node={current_node} (attempt {attempt}/{LLM_RETRY_MAX_ATTEMPTS})")
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                try:
                    response_data = response.json()
                except Exception:
                    logger.warning(f"LLM route returned non-JSON: HTTP {response.status_code} - {response.text[:500]} (attempt {attempt})")
                    if response.status_code in (502, 503, 504) and attempt < LLM_RETRY_MAX_ATTEMPTS:
                        await asyncio.sleep(LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)])
                        continue
                    return None

                if response.status_code == 200 and response_data.get("success"):
                    logger.info(f"LLM route response received for user {user_id}: action={response_data.get('action')}")
                    return response_data

                if response.status_code in (429, 502, 503, 504) and attempt < LLM_RETRY_MAX_ATTEMPTS:
                    wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                    logger.warning(f"LLM route HTTP {response.status_code} (attempt {attempt}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                logger.error(f"LLM route error: HTTP {response.status_code} - {response_data}")
                return None

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < LLM_RETRY_MAX_ATTEMPTS:
                wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                logger.warning(f"LLM route {type(e).__name__} (attempt {attempt}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"LLM route failed after {LLM_RETRY_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")
        except Exception as e:
            if attempt < LLM_RETRY_MAX_ATTEMPTS:
                wait = LLM_RETRY_BACKOFF[min(attempt - 1, len(LLM_RETRY_BACKOFF) - 1)]
                logger.warning(f"LLM route error (attempt {attempt}): {type(e).__name__}: {e}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"LLM route request error after {LLM_RETRY_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")

    return None


async def call_generic(
    user_id: str,
    phone_number: str,
    message: str,
    user_name: str,
    current_node: str,
) -> Optional[dict]:
    """Compatibility wrapper that always dispatches through `/api/v1/route`."""
    # Pass an empty expected_input since we are just doing generic conversational routing
    res = await call_route(
        user_id=user_id,
        phone_number=phone_number,
        message=message,
        user_name=user_name,
        current_node=current_node,
        expected_input=None,
    )
    if not res:
        return None
    
    action = res.get("action")
    
    legacy_response = {
        "success": True,
        "suggested_node": res.get("suggested_node"),
        "detected_intent": res.get("intent"),
        "confidence": res.get("confidence"),
        "processing_time_ms": res.get("processing_time_ms")
    }
    
    if action == "answer" and res.get("answer"):
        legacy_response["response"] = res["answer"].get("text", "")
    elif action == "clarify":
        clarification = res.get("clarification")
        if isinstance(clarification, dict):
            legacy_response["response"] = clarification.get("text", "")
        else:
            legacy_response["response"] = str(clarification or "")
    elif action == "extract" and res.get("extracted"):
        # For legacy compatibility, if it unexpectedly extracted, just set response
        legacy_response["response"] = f"Extracted: {res['extracted'].get('value')}"
    else:
        legacy_response["response"] = ""
        
    return legacy_response


async def call_extract(
    user_id: str,
    field_name: str,
    question_asked: str,
    user_response: str,
    expected_format: str = "text",
    allowed_values: Optional[list] = None,
) -> Optional[dict]:
    """Compatibility wrapper that always dispatches through `/api/v1/route`."""
    input_type = _map_expected_format_to_type(expected_format)
    
    expected_input_payload = {
        "type": input_type,
        "format_hint": expected_format,
        "field_name": field_name
    }
    
    if allowed_values:
        expected_input_payload["type"] = "enum"
        expected_input_payload["allowed_values"] = allowed_values
    elif field_name in ("menu_intent", "navigation_intent"):
        expected_input_payload["type"] = "enum"
        expected_input_payload["allowed_values"] = [
            {"value": "buy_cover", "label": "buy cover", "synonyms": ["buy insurance", "purchase policy", "travel cover"]},
            {"value": "check_policy", "label": "check policy", "synonyms": ["view existing", "policy status", "draft policy"]},
            {"value": "welcome_submit_boarding", "label": "submit boarding pass", "synonyms": ["upload boarding pass"]},
            {"value": "welcome_get_support", "label": "get support", "synonyms": ["help", "question"]}
        ]

    res = await call_route(
        user_id=user_id,
        phone_number=user_id, # Fallback to user_id as phone
        message=user_response,
        user_name="User", # Unknown in this context
        current_node=f"extract_{field_name}",
        expected_input=expected_input_payload,
        history=[
            {"role": "bot", "text": question_asked},
            {"role": "user", "text": user_response}
        ]
    )
    
    if not res:
        return None
        
    action = res.get("action")

    legacy_response = {
        "success": True,
        "action": action,
        "is_valid": False,
        "extracted_value": None,
        "normalized_value": None,
        "guidance_message": None
    }

    if action == "extract" and res.get("extracted"):
        legacy_response["is_valid"] = True
        extracted = res["extracted"]
        legacy_response["extracted_value"] = (
            extracted.get("value")
            or extracted.get("normalized")
        )
        legacy_response["normalized_value"] = extracted.get("normalized")
    elif action == "clarify":
        clarification = res.get("clarification")
        if isinstance(clarification, dict):
            legacy_response["guidance_message"] = clarification.get("text", "")
        else:
            legacy_response["guidance_message"] = str(clarification or "")
    elif action == "answer" and res.get("answer"):
        legacy_response["guidance_message"] = res["answer"].get("text", "")

    return legacy_response


async def call_policy_flow_validate(
    field_name: str,
    question_asked: str,
    user_response: str,
    step_type: str,
    step_id: Optional[int] = None,
    context: Optional[str] = None,
    allowed_values: Optional[list] = None,
    expected_format: Optional[str] = None,
    validation_rules: Optional[dict] = None,
    context_data: Optional[dict] = None,
) -> Optional[dict]:
    """Compatibility wrapper that always dispatches through `/api/v1/route`."""
    input_type = _map_expected_format_to_type(step_type if step_type else (expected_format or ""))
    
    # Map allowed_values to the new format if provided
    formatted_allowed = None
    if allowed_values:
        formatted_allowed = []
        for val in allowed_values:
            if isinstance(val, dict):
                formatted_allowed.append(val)
            else:
                formatted_allowed.append({"value": str(val), "label": str(val), "synonyms": []})
                
    res = await call_route(
        user_id="anonymous",
        phone_number="anonymous",
        message=user_response,
        user_name="User",
        current_node=f"validate_step_{step_id}" if step_id else f"validate_{field_name}",
        expected_input={
            "type": input_type,
            "format_hint": expected_format or context or "",
            "field_name": field_name,
            "allowed_values": formatted_allowed,
            "validation_rules": validation_rules or {}
        },
        history=[
            {"role": "bot", "text": question_asked},
            {"role": "user", "text": user_response}
        ],
        session_data=context_data
    )
    
    if not res:
        return None
        
    action = res.get("action")

    legacy_response = {
        "success": True,
        "action": action,
        "is_valid": False,
        "extracted_value": None,
        "normalized_value": None,
        "guidance_message": None
    }

    if action == "extract" and res.get("extracted"):
        legacy_response["is_valid"] = True
        extracted = res["extracted"]
        legacy_response["extracted_value"] = (
            extracted.get("value")
            or extracted.get("normalized")
        )
        legacy_response["normalized_value"] = extracted.get("normalized")
    elif action == "clarify":
        clarification = res.get("clarification")
        if isinstance(clarification, dict):
            legacy_response["guidance_message"] = clarification.get("text", "")
        else:
            legacy_response["guidance_message"] = str(clarification or "")
    elif action == "answer" and res.get("answer"):
        legacy_response["guidance_message"] = res["answer"].get("text")

    return legacy_response
