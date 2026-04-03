TravelAssist - Backend Developer Guide


OVERVIEW

TravelAssist is a WhatsApp-style travel insurance chatbot frontend built with React.
All business logic runs client-side using mock functions. This guide explains what each
mock does, what inputs it expects, what it returns, and how to replace it with a real API call.

All mock functions are located in: src/data/mockData.js
All flow logic that calls those functions is in: src/context/ChatContext.js


RUNNING THE FRONTEND LOCALLY

Requirements: Node.js 16 or higher, npm

    npm install
    npm start

Opens at http://localhost:3000


MOCK FUNCTIONS TO REPLACE


1. simulateFlightLookup(flightNumber)

Purpose: Looks up a flight by its number and returns status and schedule details.

Input:  flightNumber - string, e.g. "P47123"

Expected return on success:
    {
      success: true,
      data: {
        flightNumber: string,
        airline: string,
        origin: string,
        destination: string,
        scheduledDeparture: string,
        scheduledArrival: string,
        status: "ON TIME" or "DELAYED" or "CANCELLED",
        delayMinutes: number,
        gate: string
      }
    }

Expected return on failure:
    { success: false, error: string }

Replace with: any live flight data API such as AviationStack or FlightAware.


2. simulateKYC(type, value)

Purpose: Verifies a traveller identity using BVN or NIN.

Input:
    type  - string, either "BVN" or "NIN"
    value - string, 11-digit number

Expected return on success:
    {
      success: true,
      data: {
        name: string   (full name as registered with the ID)
      }
    }

Expected return on failure:
    { success: false, error: string }

Replace with: NIBSS BVN verification API or NIN lookup via NIMC.


3. simulatePayment(method, amount)

Purpose: Processes a payment and returns success or failure.

Input:
    method - string, one of "card", "bank", "ussd", "wallet"
    amount - number, e.g. 2500

Expected return on success:
    { success: true, reference: string }

Expected return on pending:
    { success: false, status: "PENDING" }

Expected return on failure:
    { success: false, status: "FAILED", error: string }

Replace with: Paystack or Flutterwave payment APIs.


4. simulateBoardingPassVerification(fileName)

Purpose: Reads an uploaded boarding pass image or PDF and extracts flight details.

Input:  fileName - string, name of the uploaded file

Expected return on success:
    {
      success: true,
      flightNumber: string,
      date: string,
      passengerName: string
    }

Expected return on failure:
    { success: false, error: string }

Replace with: an OCR or document parsing service such as Google Vision or AWS Textract.


5. generatePolicyNumber()

Purpose: Generates a unique policy reference number after successful payment.

Current behaviour: returns a random string like "TA-2026-482910"

Replace with: a call to your policy management backend that creates the policy record
and returns the assigned policy number.


DATA MODELS

The frontend expects the following fields when displaying a policy:

    policyNumber  - string
    status        - "ACTIVE" or "EXPIRED"
    airline       - string
    flightNumber  - string
    travelDate    - string
    plan          - string, plan name
    phone         - string

Mock policies used for testing are defined in the MOCK_POLICIES array in mockData.js.


SESSION AND STATE

There is no backend session management. All state is held in React context and
optionally persisted to localStorage. When connecting a backend, you will need to
add authentication and pass a user token with each API call.


PAYMENT FLOW SUMMARY

1. User selects a cover plan
2. User completes KYC (BVN or NIN verification)
3. User selects payment method and pays
4. On payment success, generatePolicyNumber is called and the policy is shown
5. User can optionally upload a boarding pass for eligibility verification


CONTACT

For questions about the frontend implementation, refer to ChatContext.js.
Each flow section is clearly commented with the flow name.

This section has moved here: [https://facebook.github.io/create-react-app/docs/troubleshooting#npm-run-build-fails-to-minify](https://facebook.github.io/create-react-app/docs/troubleshooting#npm-run-build-fails-to-minify)
