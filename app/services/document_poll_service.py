"""Background document-URL polling service.

When a policy document is not yet available, callers can schedule a background
poll that retries ``get_policy_document_url`` every ~30 seconds for up to
5 minutes.  When the URL becomes available the bot proactively sends it to the
user.  If the document never appears within the window the poll exits silently.

An in-flight guard (keyed on ``wa_id + policy_code``) prevents duplicate
concurrent polls for the same document.
"""

import asyncio
import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service
from app.services.whatsapp_service import send_text_message

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 30
_MAX_ATTEMPTS = 10

_in_flight: set[str] = set()


def _guard_key(wa_id: str, policy_code: str) -> str:
    return f"{wa_id}:{policy_code}"


async def _poll_document(
    wa_id: str,
    policy_code: str,
    display_name: str,
    phone_number_id: Optional[str],
    source: str,
) -> None:
    """Retry fetching the document URL and push it to the user when ready.

    Runs as a fire-and-forget asyncio task.  All exceptions are caught so this
    never crashes the event loop.
    """
    key = _guard_key(wa_id, policy_code)
    try:
        masked = wa_id[:4] + "****"
        logger.info(
            "Document poll started – policy=%s user=%s max_attempts=%d interval=%ds",
            policy_code,
            masked,
            _MAX_ATTEMPTS,
            _POLL_INTERVAL_SECONDS,
        )
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            logger.debug(
                "Document poll attempt %d/%d – policy=%s user=%s",
                attempt,
                _MAX_ATTEMPTS,
                policy_code,
                masked,
            )
            try:
                doc_url = await ipurvey_service.get_policy_document_url(policy_code)
            except Exception:
                logger.exception(
                    "Document poll: error fetching URL on attempt %d – policy=%s",
                    attempt,
                    policy_code,
                )
                doc_url = None

            if doc_url:
                logger.info(
                    "Document poll: URL ready after %d attempt(s) – policy=%s user=%s",
                    attempt,
                    policy_code,
                    masked,
                )
                from app.services.whatsapp_service import send_policy_document_message
                sent = await send_policy_document_message(
                    to=wa_id,
                    doc_url=doc_url,
                    policy_code=policy_code,
                    display_name=display_name,
                    phone_number_id=phone_number_id,
                )
                if not sent:
                    body = (
                        f"📄 *Policy Document Ready* ✅\n\n"
                        f"*{display_name}*\n"
                        f"Policy No: *{policy_code}*\n\n"
                        f"Your document is now available:\n{doc_url}"
                    )
                    await send_text_message(
                        to=wa_id,
                        body=body,
                        phone_number_id=phone_number_id,
                        source=source,
                    )
                return

        logger.info(
            "Document poll: URL not available after %d attempts – policy=%s user=%s; giving up",
            _MAX_ATTEMPTS,
            policy_code,
            masked,
        )
    except Exception:
        logger.exception(
            "Document poll: unexpected error – policy=%s user=%s", policy_code, wa_id[:4] + "****"
        )
    finally:
        _in_flight.discard(key)


def schedule_document_poll(
    wa_id: str,
    policy_code: str,
    display_name: str,
    phone_number_id: Optional[str],
    source: str = "document_poll",
) -> Optional[asyncio.Task]:
    """Schedule a background document-URL poll for *wa_id* / *policy_code*.

    If a poll is already in-flight for the same (wa_id, policy_code) pair the
    call is a no-op to prevent duplicate concurrent polls.  Returns the created
    ``asyncio.Task``, or ``None`` if no event loop is running.

    Args:
        wa_id: The user's WhatsApp ID (used to send the proactive message).
        policy_code: The policy reference / code to fetch the document for.
        display_name: Human-readable label shown in the document message
            (e.g. the contact name or product name).
        phone_number_id: The WhatsApp Business phone number ID.
        source: Logging source tag forwarded to ``send_text_message``.
    """
    key = _guard_key(wa_id, policy_code)
    if key in _in_flight:
        logger.debug(
            "Document poll already in-flight for policy=%s user=%s; skipping duplicate",
            policy_code,
            wa_id[:4] + "****",
        )
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "schedule_document_poll called outside of async context – policy=%s user=%s; poll skipped",
            policy_code,
            wa_id[:4] + "****",
        )
        return None

    _in_flight.add(key)
    task = loop.create_task(
        _poll_document(wa_id, policy_code, display_name, phone_number_id, source)
    )
    task.add_done_callback(
        lambda t: logger.debug(
            "Document poll task finished – policy=%s user=%s", policy_code, wa_id[:4] + "****"
        )
    )
    return task
