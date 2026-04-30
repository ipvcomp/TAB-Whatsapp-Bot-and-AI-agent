"""
Temporary testing overrides.
Set values to None to disable. Remove this file entirely before production.
"""

# Override the MSISDN sent to Ipurvey APIs (KYC, draft, user checks).
# WhatsApp replies still go to the actual sender's number.
# Use this when you need OTP on a specific verified number without adding
# that number to Meta's allowed recipient list.
TEST_OVERRIDE_MSISDN: str | None = "+2349066662020"


def get_msisdn(wa_id: str) -> str:
    """Return the MSISDN to use for Ipurvey API calls.

    If TEST_OVERRIDE_MSISDN is set, returns that regardless of wa_id.
    Otherwise derives the MSISDN from wa_id by prepending '+'.
    """
    if TEST_OVERRIDE_MSISDN:
        return TEST_OVERRIDE_MSISDN
    return f"+{wa_id}" if not wa_id.startswith("+") else wa_id
