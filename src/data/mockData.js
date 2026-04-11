// Mock data for TravelAssist chatbot

export const MOCK_FLIGHTS = {
  P47123: {
    flightNumber: 'P47123',
    airline: 'Air Peace',
    origin: 'LOS (Lagos)',
    destination: 'ABV (Abuja)',
    scheduledDeparture: '13:40',
    scheduledArrival: '14:50',
    status: 'DELAYED',
    delayMinutes: 75,
    gate: 'B4',
    terminal: 'Domestic',
    date: '12 April 2026',
  },
  QI402: {
    flightNumber: 'QI402',
    airline: 'Ibom Air',
    origin: 'LOS (Lagos)',
    destination: 'PHC (Port Harcourt)',
    scheduledDeparture: '09:15',
    scheduledArrival: '10:20',
    status: 'ON TIME',
    delayMinutes: 0,
    gate: 'A2',
    terminal: 'Domestic',
    date: '12 April 2026',
  },
  W3501: {
    flightNumber: 'W3501',
    airline: 'Overland Airways',
    origin: 'ABV (Abuja)',
    destination: 'KAN (Kano)',
    scheduledDeparture: '11:00',
    scheduledArrival: '11:55',
    status: 'CANCELLED',
    delayMinutes: 0,
    gate: 'C1',
    terminal: 'Domestic',
    date: '12 April 2026',
  },
  AA123: {
    flightNumber: 'AA123',
    airline: 'American Airlines',
    origin: 'LOS (Lagos)',
    destination: 'ABV (Abuja)',
    scheduledDeparture: '10:30',
    scheduledArrival: '11:45',
    status: 'DELAYED',
    delayMinutes: 75,
    gate: 'B12',
    terminal: '2',
    date: '12 April 2026',
  },
  LH456: {
    flightNumber: 'LH456',
    airline: 'Lufthansa',
    origin: 'LOS (Lagos)',
    destination: 'FRA (Frankfurt)',
    scheduledDeparture: '23:15',
    scheduledArrival: '06:40',
    status: 'ON TIME',
    delayMinutes: 0,
    gate: 'C4',
    terminal: 'International',
    date: '12 April 2026',
  },
};

export const COVER_PLANS = [
  {
    id: 'basic',
    name: 'Local Travel Basic',
    emoji: '🛡️',
    price: 2500,
    provider: 'Tangerine Insurance',
    validity: 'Single trip',
    currency: 'NGN',
    features: [
      'Major delay cover',
      'Cancellation cover',
    ],
  },
  {
    id: 'premium',
    name: 'Local Travel Premium',
    emoji: '👑',
    price: 3500,
    provider: 'Tangerine Insurance',
    validity: 'Multi Trip',
    currency: 'NGN',
    features: [
      'Major delay cover',
      'Cancellation cover',
      'Missed connection cover',
    ],
  },
];

export const WALLET_PROVIDERS = [
  { id: 'wallet_9psb', label: '9PSB', emoji: '📱' },
  { id: 'wallet_smartcash', label: 'SmartCash', emoji: '💚' },
  { id: 'wallet_opay', label: 'OPay', emoji: '🟠' },
];

export const NIGERIAN_AIRPORTS = [
  { code: 'LOS', name: 'Murtala Muhammed International', city: 'Lagos' },
  { code: 'ABV', name: 'Nnamdi Azikiwe International', city: 'Abuja' },
  { code: 'PHC', name: 'Port Harcourt International', city: 'Port Harcourt' },
  { code: 'KAN', name: 'Mallam Aminu Kano International', city: 'Kano' },
  { code: 'ENU', name: 'Akanu Ibiam International', city: 'Enugu' },
  { code: 'ILR', name: 'Ilorin International', city: 'Ilorin' },
  { code: 'ABB', name: 'Asaba International', city: 'Asaba' },
  { code: 'QOW', name: 'Sam Mbakwe International', city: 'Owerri' },
  { code: 'CBQ', name: 'Margaret Ekpo International', city: 'Calabar' },
  { code: 'MIU', name: 'Maiduguri International', city: 'Maiduguri' },
  { code: 'SKO', name: 'Sadiq Abubakar III International', city: 'Sokoto' },
  { code: 'IBA', name: 'Ibadan', city: 'Ibadan' },
  { code: 'AKR', name: 'Akure Airport', city: 'Akure' },
  { code: 'YOL', name: 'Yola International', city: 'Yola' },
  { code: 'LHR', name: 'Heathrow Airport', city: 'London' },
  { code: 'DXB', name: 'Dubai International', city: 'Dubai' },
  { code: 'JNB', name: 'OR Tambo International', city: 'Johannesburg' },
  { code: 'NBO', name: 'Jomo Kenyatta International', city: 'Nairobi' },
  { code: 'ACC', name: 'Kotoka International', city: 'Accra' },
  { code: 'CDG', name: 'Charles de Gaulle Airport', city: 'Paris' },
];

export const NIGERIAN_BANKS = [
  'Access Bank', 'Citibank Nigeria', 'Ecobank Nigeria', 'Fidelity Bank',
  'First Bank of Nigeria', 'First City Monument Bank', 'Globus Bank',
  'Guaranty Trust Bank', 'Heritage Bank', 'Jaiz Bank', 'Keystone Bank',
  'Lotus Bank', 'Parallex Bank', 'Polaris Bank', 'Premium Trust Bank',
  'Providus Bank', 'Stanbic IBTC Bank', 'Standard Chartered Bank',
  'Sterling Bank', 'SunTrust Bank', 'TAJBank', 'Titan Trust Bank',
  'Union Bank of Nigeria', 'United Bank for Africa', 'Unity Bank',
  'Wema Bank', 'Zenith Bank',
  'Carbon', 'Kuda Bank', 'Moniepoint', 'OPay', 'PalmPay', 'Sparkle',
  'VFD Microfinance Bank', 'Eyowo', 'Rubies Bank', 'Mint Finex MFB',
  'GTBank Microfinance', 'Zenith Microfinance Bank', 'Zest Payments',
  'ALAT by Wema', 'Opay Digital Services', 'Flutterwave', 'Piggyvest',
  'Fairmoney Microfinance Bank', 'Accion Microfinance Bank',
  'AB Microfinance Bank', 'CEMCS Microfinance Bank', 'Covenant MFB',
  'Ekondo Microfinance Bank', 'Finatrust Microfinance Bank',
  'Grooming Microfinance Bank', 'Hasal Microfinance Bank',
  'Ibile Microfinance Bank', 'Lapo Microfinance Bank',
  'Mainstreet Microfinance Bank', 'Mutual Benefits Microfinance Bank',
  'NPF Microfinance Bank', 'Okpoga Microfinance Bank',
  'Page Financials', 'Pecantrust Microfinance Bank',
  'Rephidim Microfinance Bank', 'Seed Capital Microfinance Bank',
  'Susu Microfinance Bank', 'Tangerine Money MFB',
  'TCF Microfinance Bank', 'Think Finance Microfinance Bank',
  'Vestige Finance MFB', 'Xslnce Microfinance Bank',
  'Yes Microfinance Bank', 'Zikora Microfinance Bank',
];

export const MOCK_POLICIES = [
  {
    policyNumber: 'TA-2026-001234',
    plan: 'Local Travel Basic',
    status: 'ACTIVE',
    flightNumber: 'LH456',
    airline: 'Lufthansa',
    coverAmount: '₦75,000',
    issueDate: '28 Mar 2026',
    expiryDate: '13 Apr 2026',
    passengerName: 'Yusuf Usman',
    phone: '08012345678',
    travelDate: '12 April 2026',
    linkedFlight: true,
  },
  {
    policyNumber: 'TA-2026-000891',
    plan: 'Local Travel Premium',
    status: 'EXPIRED',
    flightNumber: 'P47100',
    airline: 'Air Peace',
    coverAmount: '₦25,000',
    issueDate: '14 Feb 2026',
    expiryDate: '16 Feb 2026',
    passengerName: 'Yusuf Usman',
    phone: '08098765432',
    travelDate: '15 Feb 2026',
    linkedFlight: false,
  },
];

export const FAQ_ITEMS = [
  {
    id: 'buying_cover',
    question: '🛒 Buy cover',
    answer:
      'Tap "Buy cover" from the main menu. Enter your trip details, complete a quick identity check, then pay. The whole process takes under 3 minutes.',
  },
  {
    id: 'kyc_needed',
    question: '🪪 KYC verification',
    answer:
      'You can verify using either your BVN or NIN.\n\nWe only use this information to confirm your identity for policy issuance and fraud prevention.\n\nMake sure the number entered belongs to the traveller buying the policy.',
  },
  {
    id: 'payment_issues',
    question: '💳 Payment issues',
    answer:
      'If your payment failed, try again or switch methods (card, bank transfer, USSD, or wallet). If the issue persists, type 00 for the main menu or speak to an agent.',
  },
  {
    id: 'my_policy',
    question: '📄 My policy',
    answer:
      'Go to "Check my policy" from the main menu. You can look up your policy by phone number, policy number, or flight number. You can also download your policy document or manage alerts.',
  },
  {
    id: 'boarding_pass',
    question: '🛂 Boarding pass upload',
    answer:
      'Usually you do not need to upload a boarding pass — payouts are automatic. We may request it only if extra verification is required. Accepted formats: JPEG, PDF, GIF, TIFF, PNG. Max size: 20MB.',
  },
  {
    id: 'claim_support',
    question: '📋 Claim support',
    answer:
      'TravelAssist detects disruptions automatically and processes payouts without you needing to file a claim. If you believe you are eligible but have not received a payout, please speak to an agent.',
  },
  {
    id: 'contact_agent',
    question: '🤝 Speak to an agent',
    answer:
      'You can reach our support team 24/7:\n🌐 www.travelassist.ng\n📧 support@travelassist.ng\n📱 WhatsApp: +234 800 TRAVEL',
  },
];

export function generatePolicyNumber() {
  const year = new Date().getFullYear();
  const num = Math.floor(100000 + Math.random() * 900000);
  return `TA-${year}-${num}`;
}

export function simulateFlightLookup(flightNumber) {
  return new Promise((resolve) => {
    setTimeout(() => {
      const flight = MOCK_FLIGHTS[flightNumber.toUpperCase()];
      if (flight) {
        resolve({ success: true, data: flight });
      } else {
        resolve({ success: false, error: 'Flight not found' });
      }
    }, 1500);
  });
}

export function simulateKYC(type, value) {
  return new Promise((resolve) => {
    setTimeout(() => {
      const pass = Math.random() > 0.2;
      if (pass) {
        resolve({
          success: true,
          data: { name: 'Yusuf Usman', dob: '1990-05-15', verified: true },
        });
      } else {
        resolve({ success: false, error: 'Verification failed. Details could not be confirmed.' });
      }
    }, 2500);
  });
}

export function simulatePayment(method, amount) {
  return new Promise((resolve) => {
    const delay = method === 'ussd' ? 4000 : 2000;
    setTimeout(() => {
      const rand = Math.random();
      if (rand > 0.15) {
        resolve({ success: true, status: 'PAID', transactionId: `TXN${Date.now()}` });
      } else if (rand > 0.05) {
        resolve({ success: false, status: 'PENDING', message: 'Payment is being processed. Please wait.' });
      } else {
        resolve({ success: false, status: 'FAILED', message: 'Payment declined. Please try again or use a different method.' });
      }
    }, delay);
  });
}

export function simulateBoardingPassVerification(fileName) {
  return new Promise((resolve) => {
    setTimeout(() => {
      const pass = Math.random() > 0.15;
      if (pass) {
        resolve({
          success: true,
          flightNumber: 'P47123',
          origin: 'LOS (Lagos)',
          destination: 'ABV (Abuja)',
          date: '12 April 2026',
        });
      } else {
        resolve({ success: false, error: 'Could not read boarding pass clearly. Please upload a clearer image.' });
      }
    }, 2000);
  });
}
