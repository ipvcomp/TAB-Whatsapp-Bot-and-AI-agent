# TravelAssist WhatsApp Bot — Static Error Messages Reference

All user-facing error and validation messages defined in the bot codebase, organized by module and trigger condition.

---

## 1. Buy Cover Flow (`buy_cover_flow_service.py`)

### 1.1 Traveler Count
| Trigger | Message Shown to User |
|---|---|
| User types something other than 1–4 | `⚠️ Please type a number between 1 and 4:`<br>`_Example: 2_` |

### 1.2 Primary Traveler Name
| Trigger | Message Shown to User |
|---|---|
| Input looks like an email address | `⚠️ That looks like an email address — please enter your *name* instead.`<br>`_Example: Yusuf Abdullahi_` |
| Only one word entered (no surname) | `⚠️ Please enter *both your first name and surname*.`<br>`_Example: Yusuf Abdullahi_` |
| Name too short or otherwise invalid | `⚠️ Please enter a valid *full name* (first name and surname).`<br>`_Example: Yusuf Abdullahi_` |
| API call to save name failed | `⚠️ We couldn't update your name — please try again.` |
| Name accepted but API save failed | `⚠️ We couldn't save your name — please enter your *full name*` |

### 1.3 Additional Traveler Name
| Trigger | Message Shown to User |
|---|---|
| Input looks like an email address | `⚠️ That looks like an email address — please enter the traveler's *name* instead.`<br>`_Example: Amina Bello_` |
| Only one word entered | `⚠️ Please enter *both first name and surname* for this traveler.`<br>`_Example: Amina Bello_` |
| Name too short or invalid | `⚠️ Please enter a valid *full name* (first name and surname).`<br>`_Example: Amina Bello_` |
| API save for traveler failed | `⚠️ We couldn't save this traveler's name — please enter their *full name*` |

### 1.4 Email Address
| Trigger | Message Shown to User |
|---|---|
| Email format invalid | `⚠️ Please enter a valid email address` |

### 1.5 Booking Reference
| Trigger | Message Shown to User |
|---|---|
| No input or empty | `Please enter your booking reference to continue.` |

### 1.6 Flight Number
| Trigger | Message Shown to User |
|---|---|
| Does not match pattern (1–2 letters + 1–6 digits) | `✈️ I couldn't recognise that flight number`<br>`Please enter it like this: *P47123*` |

### 1.7 Departure Date
| Trigger | Message Shown to User |
|---|---|
| Date format not recognised | `📅 Please enter the date like this: *12 April 2026*` |
| Date is in the past | `⚠️ Please enter today's date or a future travel date` |

### 1.8 Departure Time
| Trigger | Message Shown to User |
|---|---|
| Time format not recognised | `⏰ Please enter a valid departure time` |
| Departure time is after arrival time (same-day flight) | `⚠️ Departure time must be *before* arrival time` |

### 1.9 Departure Airport Search
| Trigger | Message Shown to User |
|---|---|
| Search returns no results | `❌ *No airports found matching "{search_term}"*`<br>`We couldn't find any airport matching your entry.`<br>`Please check the spelling or try searching again.` |

### 1.10 Arrival Date
| Trigger | Message Shown to User |
|---|---|
| Date format not recognised | `📅 Please enter the arrival date like this: *12 April 2026*` |
| Date is in the past | `⚠️ Arrival date cannot be in the past`<br>`Please enter today's date or a future arrival date` |
| Arrival date is before departure date | `⚠️ Arrival date cannot be before your departure date` |

### 1.11 Arrival Time
| Trigger | Message Shown to User |
|---|---|
| Time format not recognised | `⏰ Please enter a valid arrival time` |
| Arrival time before departure time on same-day flight | `⚠️ Arrival time must be *after* departure time on the same day` |

### 1.12 Arrival Airport Search
| Trigger | Message Shown to User |
|---|---|
| Search returns no results | `❌ *No airports found matching "{search_term}"*`<br>`We couldn't find any airport matching your entry.`<br>`Please check the spelling or try searching again.` |

### 1.13 Submit Itinerary (API)
| Trigger | Message Shown to User |
|---|---|
| API returns error (e.g. booking ref already used) | `⚠️ *We couldn't submit your trip details*`<br>`_{API error message, e.g. "An active policy already exists with booking reference: AB1XY2"}_`<br>`Please check your flight details and try again, or edit them if something is incorrect.` |
| Exception / network failure | `⚠️ *We're unable to complete that right now*`<br>`Please try again shortly` |

### 1.14 Fetch Quotes / Cover Load
| Trigger | Message Shown to User |
|---|---|
| API returns empty quotes list | `⚠️ *We're unable to load covers right now*`<br>`Please try again shortly` |
| No quotes at all after retry | `⚠️ *No covers available*`<br>`Please try again shortly` |
| Cover selection API fails | `⚠️ *We couldn't confirm your cover selection*` |
| Generic exception on cover step | `⚠️ *We're unable to complete that right now*`<br>`Please try again shortly` |

### 1.15 Incomplete / Resumable Draft
| Trigger | Message Shown to User |
|---|---|
| Existing draft found via `resume_draft_policy` (state ≠ DRAFT) | `📋 *Incomplete Application Found*`<br>`We found an unfinished policy from your previous session:`<br>`{hint with flight/traveler details}`<br>`Would you like to continue where you left off?` |
| Existing draft found via `create_draft_policy` (state ≠ DRAFT) | `📋 *Incomplete Application Found*`<br>`We found an unfinished policy from your previous session.`<br>`Would you like to continue where you left off?` |

---

## 2. Payment Flow (`payment_flow_service.py`)

### 2.1 Bank Account Number
| Trigger | Message Shown to User |
|---|---|
| Not exactly 10 digits | `⚠️ Please enter a valid *10-digit account number*:`<br>`_Example: 0123456789_` |

### 2.2 Bank Name Search
| Trigger | Message Shown to User |
|---|---|
| Less than 2 characters entered | `⚠️ Enter *at least 2 characters*. _Example: Zen, GT_` |
| Less than 3 characters on retry | `Enter at least 3 characters of your bank name:`<br>`_Example: Zen, GT_` |
| User types text instead of selecting from list | `⚠️ Please select a bank from the list.` |

### 2.3 Wallet Phone Number
| Trigger | Message Shown to User |
|---|---|
| Number too short or non-numeric | `⚠️ Enter a valid *phone number*:`<br>`_Example: 08012345678_` |

### 2.4 Payment Method
| Trigger | Message Shown to User |
|---|---|
| User selects a method other than Bank Transfer | `⚠️ *Payment method not currently available*`<br>`Bank transfer is the only supported payment method at this time.`<br>`Please select *Bank Transfer* to continue.` |
| Payment method unavailable (second occurrence) | `⚠️ *Payment method not currently available*`<br>`Only Bank Transfer is supported. Please select it to continue.` |

### 2.5 Payment Initiation
| Trigger | Message Shown to User |
|---|---|
| Bank transfer initiation API fails | `⚠️ *Payment initiation failed*`<br>`We could not start the bank transfer process.`<br>`Please try again or contact support.` |

### 2.6 Policy Submission
| Trigger | Message Shown to User |
|---|---|
| Policy submission API fails (first attempt) | `⚠️ *Policy Submission Failed*`<br>`We were unable to submit your policy at this time.`<br>`_{error details}_`<br>`Please tap *Retry* to try again, or contact support if the problem persists.` |
| Policy submission API fails (retry attempt) | `⚠️ *Policy Submission Failed*`<br>`_{error details}_`<br>`Tap Retry to try again or go to the main menu:` |

---

## 3. KYC Flow (`kyc_flow_service.py`)

### 3.1 ID Number Input
| Trigger | Message Shown to User |
|---|---|
| User sends non-numeric or wrong length | `Please type your ID number to continue.` |

### 3.2 KYC Initiation
| Trigger | Message Shown to User |
|---|---|
| API fails to initiate KYC (BVN/NIN) | `⚠️ *Verification Incomplete*`<br>`Please choose:` (with Retry / Cancel buttons) |

### 3.3 OTP Verification
| Trigger | Message Shown to User |
|---|---|
| OTP resend / re-prompt | `Please enter the *6-digit OTP* sent to your phone:` |
| Wrong OTP or expired | `❌ *Incorrect OTP*`<br>`The code you entered is incorrect or has expired.`<br>`Please try again or request a new OTP:` |
| KYC verify API fails | `⚠️ *Verification Incomplete*`<br>`Please choose:` (with Retry / Cancel buttons) |

---

## 4. Boarding Pass Flow (`bp_link_flow_service.py`)

### 4.1 Policy Lookup
| Trigger | Message Shown to User |
|---|---|
| No active policies linked to user's number | `⚠️ We couldn't find any active policies linked to your number.`<br>`Please contact support if you believe this is an error.` |
| Selected policy has expired | `⚠️ *This policy has expired*`<br>`Boarding pass upload is only available for active policies.`<br>`Please select an active policy or return to the main menu.` |
| Policy details not found during status check | `⚠️ Could not check status — policy details not found. Please contact support.` |

### 4.2 File Upload
| Trigger | Message Shown to User |
|---|---|
| User sends text/audio instead of image or PDF | `⚠️ Please *send an image or PDF* of your boarding pass.` |
| Boarding pass rejected by API | `❌ *Boarding pass rejected*`<br>`Please upload a clearer image. Make sure all details are visible.` |

---

## 5. Check Policy Flow (`check_policy_flow_service.py`)

### 5.1 Flight Number Search
| Trigger | Message Shown to User |
|---|---|
| Invalid flight number format | `⚠️ Please enter a valid flight number.`<br>`_Example: P47123_` |
| No policy found matching flight + date | `⚠️ No matching policy found. Please try again or use a different search method.` |

### 5.2 Departure Date (Search)
| Trigger | Message Shown to User |
|---|---|
| Date not recognised | `⚠️ Please enter your departure date.`<br>`_Example: 12 April 2026_` |

### 5.3 Policy Number Search
| Trigger | Message Shown to User |
|---|---|
| Invalid format | `⚠️ Please enter a valid policy number.`<br>`_Example: TA-238491_` |
| No policy found for that reference | `⚠️ No policy found for *{ref}*.`<br>`Please double-check the number and try again.` |

### 5.4 General
| Trigger | Message Shown to User |
|---|---|
| No policies found for this phone number | `⚠️ No policies found linked to your phone number.` |
| Empty result after all search attempts | `⚠️ No policies found.` |

---

## 6. Update Details Flow (`update_details_flow_service.py`)

### 6.1 First Name
| Trigger | Message Shown to User |
|---|---|
| Less than 2 characters | `⚠️ Please enter a valid *first name* (at least 2 characters):`<br>`_Example: Samuel_` |

### 6.2 Last Name
| Trigger | Message Shown to User |
|---|---|
| Less than 2 characters | `⚠️ Please enter a valid *last name* (at least 2 characters):`<br>`_Example: Olamide_` |

### 6.3 Full Name (Legacy Traveler Sub-flow)
| Trigger | Message Shown to User |
|---|---|
| Less than 3 characters | `⚠️ Please enter the *full name* (at least 3 characters):`<br>`_Example: John Adewale Doe_` |

### 6.4 Email Address
| Trigger | Message Shown to User |
|---|---|
| Invalid format | `⚠️ Please enter a valid *email address*:`<br>`_Example: john.doe@gmail.com_` |

### 6.5 Bank Account Number
| Trigger | Message Shown to User |
|---|---|
| Not 9–11 digits | `⚠️ Enter a valid *10-digit account number*:`<br>`_Example: 0123456789_` |

### 6.6 Bank Name Search
| Trigger | Message Shown to User |
|---|---|
| Less than 2 characters | `⚠️ Enter *at least 2 characters*. _Example: Zen, GT_` |
| User types text instead of selecting from list | `⚠️ Please select a bank from the list.` |

### 6.7 Wallet Phone Number
| Trigger | Message Shown to User |
|---|---|
| Less than 10 digits or non-numeric | `⚠️ Enter a valid *phone number*:`<br>`_Example: 08012345678_` |

---

## 7. Auto-Reply / Welcome (`auto_reply_service.py`)

> These messages are not error messages per se but are important system responses shown outside of active flows.

| Trigger | Message Shown to User |
|---|---|
| User types unrecognised input (no active flow, LLM off) | Default reply explaining how to use the bot |
| Media received (image/video/doc) | `Thanks for sending that! 📎 We've received your media. Our team will review it shortly.` |
| User sends bye/goodbye | `Goodbye! 👋 Have a great day. We're always here when you need us!` |

---

## 8. Global Utility Footer

Shown after every interactive message in all flows:

```
*Utility options:*
0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu
99 ❌ Cancel/Exit
```

---

## Notes

- **Dynamic parts** — some messages include a variable placeholder, e.g. `{search_term}`, `{ref}`, `_{API error message}_`. These are italicised in WhatsApp.
- **Retry buttons** — most API failure messages are paired with `🔄 Try again` and `✏️ Edit details` buttons.
- **WhatsApp formatting** — `*bold*`, `_italic_`, newlines (`\n`) are rendered natively by WhatsApp.
- **Source of truth** — this file reflects the codebase as of commit `935bd88`. If new flows are added, update this document accordingly.
