"""Admin utility endpoints — internal use only.

These routes are protected by the ``X-Admin-Token`` request header and are
NOT intended for public consumption.  They exist to help developers and
support staff perform one-off maintenance tasks (e.g. cleaning up stale test
drafts) without needing direct database or third-party-admin-panel access.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.core.config import get_settings
from app.services.ipurvey_api import fetch_policies_by_msisdn
from app.services.ipurvey_service import cancel_draft_policy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

DRAFT_STATUSES = {"DRAFT", "IN_PROGRESS", "PENDING", "INCOMPLETE"}


def _require_admin(token: Optional[str]) -> None:
    settings = get_settings()
    admin_secret = settings.ADMIN_SECRET
    if not admin_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin endpoints are disabled (ADMIN_SECRET not configured).",
        )
    if token != admin_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token.",
        )


@router.delete(
    "/drafts",
    summary="Cancel all draft policies for an MSISDN",
    description=(
        "Fetches every policy linked to the given MSISDN, identifies those whose "
        "status is DRAFT / IN_PROGRESS / PENDING / INCOMPLETE, and cancels each one "
        "via the Ipurvey API.  Useful for clearing accumulated test data before "
        "payment-flow testing so the 'Incomplete Application Found' prompt shows "
        "the correct most-recent draft only.\n\n"
        "**This endpoint is protected by the `X-Admin-Token` header.**"
    ),
)
async def cancel_stale_drafts(
    msisdn: str = Query(
        ...,
        description=(
            "The MSISDN (phone number) whose draft policies should be cancelled. "
            "Include the country code with or without a leading '+' "
            "(e.g. 2348012345678 or +2348012345678)."
        ),
    ),
    all_drafts: bool = Query(
        True,
        description=(
            "When True (default) ALL policies whose status is in "
            f"{sorted(DRAFT_STATUSES)} are cancelled.  "
            "Set to False to perform a dry-run: the response will list the drafts "
            "that *would* be cancelled without actually cancelling them."
        ),
    ),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    _require_admin(x_admin_token)

    logger.info(
        "[admin] cancel_stale_drafts msisdn=%s dry_run=%s", msisdn, not all_drafts
    )

    policies = await fetch_policies_by_msisdn(msisdn)

    if not policies:
        return {
            "msisdn": msisdn,
            "total_fetched": 0,
            "drafts_found": 0,
            "cancelled": 0,
            "failed": 0,
            "skipped": 0,
            "dry_run": not all_drafts,
            "detail": "No policies found for this MSISDN.",
            "results": [],
        }

    drafts = [
        p for p in policies if p.get("status", "").upper() in DRAFT_STATUSES
    ]
    non_drafts = len(policies) - len(drafts)

    cancelled_ids: list[str] = []
    failed_ids: list[str] = []
    results: list[dict] = []

    for policy in drafts:
        pid = policy.get("id", "")
        ref = policy.get("ref", pid)
        entry: dict = {
            "id": pid,
            "ref": ref,
            "status": policy.get("status"),
            "action": "dry_run" if not all_drafts else None,
        }

        if all_drafts:
            ok = await cancel_draft_policy(pid)
            entry["action"] = "cancelled" if ok else "failed"
            if ok:
                cancelled_ids.append(pid)
            else:
                failed_ids.append(pid)

        results.append(entry)

    logger.info(
        "[admin] cancel_stale_drafts done — found=%d cancelled=%d failed=%d skipped_non_draft=%d",
        len(drafts),
        len(cancelled_ids),
        len(failed_ids),
        non_drafts,
    )

    return {
        "msisdn": msisdn,
        "total_fetched": len(policies),
        "drafts_found": len(drafts),
        "cancelled": len(cancelled_ids),
        "failed": len(failed_ids),
        "skipped_non_draft": non_drafts,
        "dry_run": not all_drafts,
        "results": results,
    }
