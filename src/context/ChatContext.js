/* eslint-disable react-hooks/exhaustive-deps, no-unused-vars, no-useless-escape */
import React, { createContext, useContext, useReducer, useCallback, useRef } from 'react';
import { STEPS } from '../data/flows';
import {
  simulateFlightLookup,
  simulateKYC,
  simulatePayment,
  simulateBoardingPassVerification,
  generatePolicyNumber,
  COVER_PLANS,
  MOCK_POLICIES,
  NIGERIAN_BANKS,
  NIGERIAN_AIRPORTS,
  WALLET_PROVIDERS,
  FAQ_ITEMS,
} from '../data/mockData';

const ChatContext = createContext(null);

const initialState = {
  messages: [],
  currentStep: STEPS.WELCOME_GREET,
  isTyping: false,
  userData: {
    name: null,
    email: null,
    // Buy cover trip data
    coverType: null,        // 'solo' | 'group'
    travellerCount: 1,
    travellerNames: [],
    tripType: null,         // 'oneway' | 'return'
    bookingRef: null,
    flightNumber: null,
    flightData: null,
    travelDate: null,
    departTime: null,
    arriveTime: null,
    departAirport: null,
    arriveAirport: null,
    carrier: null,
    selectedPlan: null,
    // KYC
    kycType: null,
    kycValue: null,
    kycVerified: false,
    kycName: null,
    // Payment
    paymentMethod: null,
    walletProvider: null,
    walletPhone: null,
    policyNumber: null,
    // Payout (how user receives money)
    payoutMethod: null,     // 'bank' | 'wallet'
    payoutBankAccount: null,
    payoutBankName: null,
    payoutWalletProvider: null,
    payoutWalletPhone: null,
    // Boarding
    boardingPassUploaded: false,
  },
  flowStack: [],
  inputMode: null,
  inputPlaceholder: '',
  isFirstVisit: true,
  sessionStarted: false,
  retryCounters: { kyc: 0, payment: 0 },
};

// Try restore from localStorage
function loadState() {
  try {
    const saved = localStorage.getItem('travelassist_state');
    if (saved) {
      const parsed = JSON.parse(saved);
      // Restore only persistent data, not UI state
      return {
        ...initialState,
        userData: parsed.userData || initialState.userData,
        isFirstVisit: false,
        sessionStarted: false,
      };
    }
  } catch (e) {}
  return initialState;
}

function reducer(state, action) {
  switch (action.type) {
    case 'ADD_MESSAGE':
      return { ...state, messages: [...state.messages, action.payload] };
    case 'SET_TYPING':
      return { ...state, isTyping: action.payload };
    case 'SET_STEP':
      return { ...state, currentStep: action.payload };
    case 'SET_INPUT_MODE':
      return { ...state, inputMode: action.payload.mode, inputPlaceholder: action.payload.placeholder || '' };
    case 'UPDATE_USER_DATA':
      return { ...state, userData: { ...state.userData, ...action.payload } };
    case 'PUSH_FLOW_STACK':
      return { ...state, flowStack: [...state.flowStack, action.payload] };
    case 'POP_FLOW_STACK': {
      const stack = [...state.flowStack];
      const prev = stack.pop();
      return { ...state, flowStack: stack, currentStep: prev || STEPS.MAIN_MENU };
    }
    case 'SET_SESSION_STARTED':
      return { ...state, sessionStarted: true };
    case 'INCREMENT_RETRY':
      return {
        ...state,
        retryCounters: {
          ...state.retryCounters,
          [action.payload]: (state.retryCounters[action.payload] || 0) + 1,
        },
      };
    case 'RESET_RETRY':
      return {
        ...state,
        retryCounters: { ...state.retryCounters, [action.payload]: 0 },
      };
    case 'RESET':
      return { ...initialState, isFirstVisit: false, messages: [] };
    default:
      return state;
  }
}

let msgIdCounter = 0;
function makeMessage(sender, content, type = 'text', extra = {}) {
  return {
    id: ++msgIdCounter,
    sender, // 'bot' | 'user'
    content,
    type, // 'text' | 'buttons' | 'list' | 'card' | 'status' | 'file' | 'summary'
    timestamp: new Date(),
    ...extra,
  };
}

export function ChatProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, null, loadState);
  const stateRef = useRef(state);
  const processingRef = useRef(false);
  stateRef.current = state;

  // Persist userData
  React.useEffect(() => {
    try {
      localStorage.setItem('travelassist_state', JSON.stringify({ userData: state.userData }));
    } catch (e) {}
  }, [state.userData]);

  // addMessage kept for potential direct use in extended flows
  // const addMessage = useCallback((sender, content, type = 'text', extra = {}) => {
  //   dispatch({ type: 'ADD_MESSAGE', payload: makeMessage(sender, content, type, extra) });
  // }, []);

  const botSay = useCallback(
    (content, type = 'text', extra = {}, delay = 600) => {
      return new Promise((resolve) => {
        dispatch({ type: 'SET_TYPING', payload: true });
        setTimeout(() => {
          dispatch({ type: 'SET_TYPING', payload: false });
          dispatch({ type: 'ADD_MESSAGE', payload: makeMessage('bot', content, type, extra) });
          resolve();
        }, delay);
      });
    },
    []
  );

  const userSay = useCallback(
    (content, type = 'text', extra = {}) => {
      dispatch({ type: 'ADD_MESSAGE', payload: makeMessage('user', content, type, extra) });
    },
    []
  );

  const setStep = useCallback((step) => {
    dispatch({ type: 'SET_STEP', payload: step });
  }, []);

  const setInputMode = useCallback((mode, placeholder = '') => {
    dispatch({ type: 'SET_INPUT_MODE', payload: { mode, placeholder } });
  }, []);

  const updateUserData = useCallback((data) => {
    dispatch({ type: 'UPDATE_USER_DATA', payload: data });
  }, []);

  const pushFlowStack = useCallback((step) => {
    dispatch({ type: 'PUSH_FLOW_STACK', payload: step });
  }, []);

  // ─── FLOW HANDLERS ─────────────────────────────────────────────────────────

  const showMainMenu = useCallback(
    async (isReturning = false) => {
      dispatch({ type: 'SET_STEP', payload: STEPS.MAIN_MENU });
      setInputMode(null);
      const greeting = isReturning
        ? '👋 Welcome back to *TravelAssist*\n\nWhat would you like to do?'
        : '😊 What would you like to do?';
      await botSay(greeting, 'buttons', {
        buttons: [
          { id: 'buy_cover',      label: '✈️ Buy cover' },
          { id: 'check_policy',   label: '📄 Check my policy' },
          { id: 'update_details', label: '✏️ Update my details' },
          { id: 'upload_boarding',label: '🛂 Upload boarding pass' },
          { id: 'payment_options',label: '💳 Payment options' },
          { id: 'help',           label: '🙋 Help' },
        ],
      });
    },
    [botSay, setInputMode]
  );

  const startSession = useCallback(async () => {
    if (stateRef.current.sessionStarted) return;
    dispatch({ type: 'SET_SESSION_STARTED' });
    const isReturning = !stateRef.current.isFirstVisit;

    if (isReturning) {
      await botSay(
        '👋 *Welcome back to TravelAssist*\n\nWhat would you like to do?',
        'text', {}, 800
      );
      await showMainMenu(true);
    } else {
      await botSay(
        '👋 *Welcome to TravelAssist*\n\nWe help travellers:\n✈️ buy travel disruption cover\n🔔 get flight alerts\n\nWhat would you like to do today?',
        'text', {}, 1000
      );
      await botSay(
        '💡 *Quick tip:*\n• *0* ↩️ Back\n• *9* 🏠 Main menu\n• *00* 🏠 Main menu\n• *99* ❌ Cancel',
        'text', {}, 600
      );
      await showMainMenu(false);
    }
  }, [botSay, showMainMenu]);

  // ─── BUY COVER FLOW ────────────────────────────────────────────────────────

  const startBuyCover = useCallback(async () => {
    userSay('✈️ Buy cover');
    pushFlowStack(STEPS.MAIN_MENU);
    setStep(STEPS.BUY_COVER_TYPE);
    await botSay(
      '✈️ *Great choice — let\'s protect your trip*\n\nThis will only take a few steps ⏱️\n\nIs this cover for:',
      'buttons',
      {
        buttons: [
          { id: 'cover_solo',  label: '👤 Just me' },
          { id: 'cover_group', label: '👥 Me and others' },
        ],
      }
    );
  }, [botSay, pushFlowStack, setStep, userSay]);

  const handleCoverType = useCallback(async (typeId) => {
    const isSolo = typeId === 'cover_solo';
    updateUserData({ coverType: isSolo ? 'solo' : 'group', travellerCount: isSolo ? 1 : null, travellerNames: [] });
    userSay(isSolo ? '👤 Just me' : '👥 Me and others');

    if (isSolo) {
      updateUserData({ travellerCount: 1 });
      setStep(STEPS.BUY_EMAIL);
      await botSay(
        '📧 Please enter your *email address*\n\nWe\'ll send your policy documents here.',
        'text'
      );
      setInputMode('text', 'e.g. name@email.com');
    } else {
      setStep(STEPS.BUY_TRAVELLER_COUNT);
      await botSay(
        '👥 How many travellers are covered?\n\nPlease reply with a number\n📌 Example: *2*',
        'text'
      );
      setInputMode('text', 'Enter number of travellers');
    }
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleTravellerCount = useCallback(async (value) => {
    const count = parseInt(value);
    if (isNaN(count) || count < 1 || count > 9) {
      await botSay('⚠️ Please enter a valid number\nExample: 2', 'text');
      return;
    }
    userSay(value);
    updateUserData({ travellerCount: count, travellerNames: [] });
    setInputMode(null);
    setStep(STEPS.BUY_TRAVELLER_NAMES);
    await botSay(
      `👤 Please enter *Traveller 1\'s* full name as it appears on their ticket\n\n📌 Example: *Yusuf Usman*`,
      'text'
    );
    setInputMode('text', 'First name and surname');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleTravellerName = useCallback(async (value) => {
    const names = stateRef.current.userData.travellerNames || [];
    const count = stateRef.current.userData.travellerCount || 1;
    const newNames = [...names, value.trim()];
    userSay(value);
    updateUserData({ travellerNames: newNames });

    if (newNames.length < count) {
      setStep(STEPS.BUY_MORE_TRAVELLERS);
      await botSay(
        `👤 Please enter *Traveller ${newNames.length + 1}\'s* full name as it appears on their ticket\n\n📌 Example: *Amina Bello*`,
        'text'
      );
      setInputMode('text', 'First name and surname');
    } else {
      setInputMode(null);
      setStep(STEPS.BUY_EMAIL);
      await botSay(
        '📧 Please enter your *email address*\n\nWe\'ll send your policy documents here.',
        'text'
      );
      setInputMode('text', 'e.g. name@email.com');
    }
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleEmailEntered = useCallback(async (value) => {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(value.trim())) {
      await botSay('⚠️ Please enter a valid email address.\n\n📌 Example: *name@email.com*', 'text');
      return;
    }
    userSay(value);
    updateUserData({ email: value.trim() });
    setInputMode(null);
    setStep(STEPS.BUY_TRIP_TYPE);
    await botSay(
      '📍 What type of trip is this?',
      'buttons',
      {
        buttons: [
          { id: 'trip_oneway', label: '➡️ One-way' },
          { id: 'trip_return', label: '🔁 Return' },
        ],
      }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleTripType = useCallback(async (typeId) => {
    const isReturn = typeId === 'trip_return';
    updateUserData({ tripType: isReturn ? 'return' : 'oneway' });
    userSay(isReturn ? '🔁 Return' : '➡️ One-way');
    setStep(STEPS.BUY_BOOKING_REF);
    await botSay(
      '🎫 Please enter your *booking reference*\n\n📌 Examples: *AB1XY2*, *2990FA62*\n\nType *0* to go back',
      'text'
    );
    setInputMode('text', 'e.g. AB1XY2');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleBookingRef = useCallback(async (value) => {
    if (value.trim().length < 4) {
      await botSay('⚠️ Please enter a valid booking reference.\n\n📌 Examples: *AB1XY2*, *2990FA62*', 'text');
      return;
    }
    userSay(value);
    updateUserData({ bookingRef: value.trim().toUpperCase() });
    setInputMode(null);
    setStep(STEPS.BUY_ENTER_FLIGHT);
    await botSay(
      '✈️ Please enter your *flight number*\n\n📌 Examples: *P47123*, *QI402*, *AA123*\n_(Just the flight number — no airline name)_\n\nType *0* to go back',
      'text'
    );
    setInputMode('text', 'e.g. P47123');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleTravelDate = useCallback(async (value) => {
    // Accept: 12 April 2026 | 12/04/2026 | 12/04/26 | 12-04-2026 | 12-04-26
    const normalised = value.trim();
    const isValid =
      normalised.length >= 6 &&
      (
        /\d{1,2}\s+[A-Za-z]+\s+\d{2,4}/.test(normalised) ||
        /\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}/.test(normalised)
      );
    if (!isValid) {
      await botSay('⚠️ Please enter the date like this: 12 April 2026', 'text');
      return;
    }
    userSay(value);
    updateUserData({ travelDate: value.trim() });
    setInputMode(null);
    setStep(STEPS.BUY_TRAVEL_TIME);
    await botSay(
      '⏰ *What time is your flight scheduled to depart?*\n\n📌 Example: *13:40*, *1:40 PM*',
      'text'
    );
    setInputMode('text', 'e.g. 13:40');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleTravelTime = useCallback(async (value) => {
    if (value.trim().length < 3) {
      await botSay('⚠️ Please enter a valid time.\n\n📌 Example: *13:40* or *1:40 PM*', 'text');
      return;
    }
    userSay(value);
    updateUserData({ departTime: value.trim() });
    setInputMode(null);
    setStep(STEPS.BUY_DEPART_AIRPORT_QUERY);
    await botSay(
      '✈️ *What airport are you flying from?*\n\nPlease enter the first 3 characters of the airport name or code\n📌 Example: *LOS*, *Abj*, *PHC*',
      'text'
    );
    setInputMode('text', 'e.g. LOS, Abj, PHC');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleDepartAirportQuery = useCallback(async (query) => {
    userSay(query);
    if (query.length < 3) {
      await botSay('⚠️ Please enter at least 3 characters\n📌 Example: *LOS*, *Abj*, *PHC*', 'text');
      return;
    }
    const q = query.toLowerCase();
    const matches = NIGERIAN_AIRPORTS.filter(
      (a) => a.code.toLowerCase().includes(q) || a.name.toLowerCase().includes(q) || a.city.toLowerCase().includes(q)
    ).slice(0, 5);
    if (matches.length === 0) {
      await botSay(`⚠️ No airports found for "*${query}*"\n\nPlease try again\n📌 Example: *LOS*, *Abj*, *PHC*`, 'text');
      return;
    }
    setStep(STEPS.BUY_DEPART_AIRPORT_SELECT);
    await botSay(
      '✈️ *Select your departure airport:*',
      'buttons',
      { buttons: matches.map((a) => ({ id: `airport_depart_${a.code}`, label: `${a.code} — ${a.city}` })) }
    );
  }, [botSay, setStep, userSay]);

  const handleDepartAirportSelect = useCallback(async (code) => {
    const airport = NIGERIAN_AIRPORTS.find((a) => a.code === code);
    if (!airport) return;
    userSay(`${airport.code} — ${airport.city}`);
    updateUserData({ departAirport: airport });
    setStep(STEPS.BUY_ARRIVE_TIME);
    await botSay(
      '⏰ *What time is your flight scheduled to arrive?*\n\n📌 Example: *15:30*, *3:30 PM*',
      'text'
    );
    setInputMode('text', 'e.g. 15:30');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleArriveTime = useCallback(async (value) => {
    if (value.trim().length < 3) {
      await botSay('⚠️ Please enter a valid time.\n\n📌 Example: *15:30* or *3:30 PM*', 'text');
      return;
    }
    userSay(value);
    updateUserData({ arriveTime: value.trim() });
    setInputMode(null);
    setStep(STEPS.BUY_ARRIVE_AIRPORT_QUERY);
    await botSay(
      '✈️ *What airport are you arriving at?*\n\nPlease enter the first 3 characters of the airport name or code\n📌 Example: *ABV*, *PHC*, *Kan*',
      'text'
    );
    setInputMode('text', 'e.g. ABV, PHC, Kan');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleArriveAirportQuery = useCallback(async (query) => {
    userSay(query);
    if (query.length < 3) {
      await botSay('⚠️ Please enter at least 3 characters\n📌 Example: *ABV*, *PHC*, *Kan*', 'text');
      return;
    }
    const q = query.toLowerCase();
    const matches = NIGERIAN_AIRPORTS.filter(
      (a) => a.code.toLowerCase().includes(q) || a.name.toLowerCase().includes(q) || a.city.toLowerCase().includes(q)
    ).slice(0, 5);
    if (matches.length === 0) {
      await botSay(`⚠️ No airports found for "*${query}*"\n\nPlease try again\n📌 Example: *ABV*, *PHC*, *Kan*`, 'text');
      return;
    }
    setStep(STEPS.BUY_ARRIVE_AIRPORT_SELECT);
    await botSay(
      '✈️ *Select your arrival airport:*',
      'buttons',
      { buttons: matches.map((a) => ({ id: `airport_arrive_${a.code}`, label: `${a.code} — ${a.city}` })) }
    );
  }, [botSay, setStep, userSay]);

  const handleArriveAirportSelect = useCallback(async (code) => {
    const airport = NIGERIAN_AIRPORTS.find((a) => a.code === code);
    if (!airport) return;
    userSay(`${airport.code} — ${airport.city}`);
    updateUserData({ arriveAirport: airport });
    setStep(STEPS.BUY_CARRIER);
    await botSay(
      '✈️ *Who are you flying with?*\n\n📌 Example: *Ibom Air*, *Air Peace*, *Overland*',
      'text'
    );
    setInputMode('text', 'e.g. Ibom Air, Air Peace');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleCarrier = useCallback(async (value) => {
    if (value.trim().length < 2) {
      await botSay('⚠️ Please enter the airline name\n📌 Example: *Ibom Air*, *Air Peace*', 'text');
      return;
    }
    userSay(value);
    updateUserData({ carrier: value.trim() });
    setInputMode(null);
    const ud = { ...stateRef.current.userData, carrier: value.trim() };
    const count = ud.travellerCount || 1;
    const depLabel = ud.departAirport ? ud.departAirport.city : '—';
    const arrLabel = ud.arriveAirport ? ud.arriveAirport.city : '—';
    setStep(STEPS.BUY_TRIP_SUMMARY);
    await botSay(
      `📍 *Trip Summary*\n\n✈️ Airline: *${value.trim()}*\n🛫 Route: *${depLabel} → ${arrLabel}*\n✈️ Flight: *${ud.flightNumber}*\n📅 Date: *${ud.travelDate}*\n⏰ Departure: *${ud.departTime}*\n⏰ Arrival: *${ud.arriveTime}*\n🎫 Booking ref: *${ud.bookingRef}*\n📍 Trip type: *${ud.tripType === 'return' ? 'Return ↩️' : 'One-way ➡️'}*\n👤 Traveller${count > 1 ? 's' : ''}: *${count}*\n📧 Email: *${ud.email}*\n\nPlease confirm:`,
      'buttons',
      {
        buttons: [
          { id: 'confirm_trip', label: '✅ Confirm' },
          { id: 'edit_trip',    label: '✏️ Edit trip details' },
        ],
      }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  // Collect flight number only, proceed to date (full lookup happens at confirm_trip)
  const handleFlightNumberInput = useCallback(async (value) => {
    const cleaned = value.trim().toUpperCase().replace(/[\s\-—–]/g, '');
    if (!/^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]{2,7}$/.test(cleaned)) {
      await botSay(
        '⚠️ I couldn\'t recognise that flight number\n\nPlease enter it like this: P47123',
        'text'
      );
      return;
    }
    userSay(value);
    updateUserData({ flightNumber: cleaned });
    setStep(STEPS.BUY_TRAVEL_DATE);
    await botSay(
      '📅 *What date are you flying?*\n\n📌 Example: *12 April 2026*, *12/04/2026*',
      'text'
    );
    setInputMode('text', 'e.g. 12 April 2026');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleFlightLookup = useCallback(
    async (flightNumber) => {
      setInputMode(null);
      setStep(STEPS.BUY_FLIGHT_FOUND);
      await botSay('⏳ Looking up your flight...', 'status', { statusType: 'loading' }, 400);

      const result = await simulateFlightLookup(flightNumber);

      if (!result.success) {
        // If the user already entered all trip details manually, build synthetic
        // flight data from their inputs and continue — no blocking error.
        const ud = stateRef.current.userData;
        if (ud.departAirport && ud.arriveAirport && ud.carrier && ud.travelDate) {
          const syntheticFlight = {
            flightNumber,
            airline: ud.carrier,
            origin: `${ud.departAirport.code} (${ud.departAirport.city})`,
            destination: `${ud.arriveAirport.code} (${ud.arriveAirport.city})`,
            scheduledDeparture: ud.departTime || '—',
            scheduledArrival: ud.arriveTime || '—',
            status: 'ON TIME',
            delayMinutes: 0,
            gate: '—',
            terminal: '—',
            date: ud.travelDate,
          };
          updateUserData({ flightData: syntheticFlight });
          await botSay(
            `✅ *Trip confirmed*\n\n✈️ *${flightNumber}* — ${ud.carrier}\n📍 ${syntheticFlight.origin} → ${syntheticFlight.destination}\n📅 ${ud.travelDate}\n⏰ Dep: ${syntheticFlight.scheduledDeparture} · Arr: ${syntheticFlight.scheduledArrival}`,
            'card',
            { cardType: 'flight' }
          );
          await botSay(
            '🛡️ *Select your cover plan:*',
            'buttons',
            {
              buttons: [
                { id: 'plan_basic',   label: '🛡️ Local Travel Basic — ₦2,500' },
                { id: 'plan_premium', label: '👑 Local Travel Premium — ₦3,500' },
              ],
            },
            500
          );
          setStep(STEPS.BUY_SELECT_PLAN);
          return;
        }

        await botSay(
          `⚠️ I couldn't find that flight yet\n\nPlease check the number and try again\nor type 9 for help`,
          'buttons',
          {
            buttons: [
              { id: 'retry_flight', label: '🔄 Try again' },
              { id: 'main_menu',    label: '🏠 Main menu' },
            ],
          }
        );
        setStep(STEPS.FLIGHT_NOT_FOUND);
        return;
      }

      const flight = result.data;
      updateUserData({ flightData: flight });

      // ── Guard: cancelled flight ──────────────────────────────────────────
      if (flight.status === 'CANCELLED') {
        setStep(STEPS.FLIGHT_CANCELLED_BUY);
        const statusEmoji = '❌';
        await botSay(
          `${statusEmoji} *Flight ${flight.flightNumber} is Cancelled*\n\n✈️ ${flight.airline}\n📍 ${flight.origin} → ${flight.destination}\n🕐 Was scheduled: ${flight.scheduledDeparture}\n\n⚠️ Cover cannot be purchased for a cancelled flight.\n\nWhat would you like to do?`,
          'buttons',
          {
            buttons: [
              { id: 'track_flight', label: '✈️ Track Another Flight' },
              { id: 'help', label: '💬 Get Support' },
              { id: 'main_menu', label: '🏠 Main Menu' },
            ],
          }
        );
        return;
      }

      // ── Guard: flight already departed (mock: if delay > 120 mins, treat as departed) ─
      if (flight.delayMinutes > 120) {
        setStep(STEPS.FLIGHT_DEPARTED_BUY);
        await botSay(
          `⚠️ *Flight ${flight.flightNumber} Has Already Departed*\n\n✈️ ${flight.airline}\n📍 ${flight.origin} → ${flight.destination}\n🕐 Departed: ${flight.scheduledDeparture}\n\n⚠️ Cover cannot be purchased after a flight has departed.\n\nWhat would you like to do?`,
          'buttons',
          {
            buttons: [
              { id: 'track_flight', label: '✈️ Track Flight' },
              { id: 'help', label: '💬 Get Support' },
              { id: 'main_menu', label: '🏠 Main Menu' },
            ],
          }
        );
        return;
      }

      // ── Guard: duplicate active policy for same flight ───────────────────
      const hasDuplicate = MOCK_POLICIES.some(
        (p) => p.status === 'ACTIVE' && p.flightNumber === flight.flightNumber
      );
      if (hasDuplicate) {
        setStep(STEPS.POLICY_DUPLICATE);
        await botSay(
          `⚠️ *You Already Have Cover for This Flight*\n\n✈️ Flight *${flight.flightNumber}* already has an active policy.\n\nPurchasing a duplicate policy is not allowed.`,
          'buttons',
          {
            buttons: [
              { id: 'check_policy', label: '📋 View Existing Policy' },
              { id: 'buy_cover', label: '🛡️ Cover a Different Flight' },
              { id: 'main_menu', label: '🏠 Main Menu' },
            ],
          }
        );
        return;
      }

      const statusEmoji = flight.status === 'ON TIME' ? '✅' : flight.status === 'DELAYED' ? '⚠️' : '❌';

      await botSay(
        `✅ *Flight Found!*\n\n✈️ *${flight.flightNumber}* — ${flight.airline}\n📍 ${flight.origin} → ${flight.destination}\n🕐 Departure: ${flight.scheduledDeparture}\n📊 Status: ${statusEmoji} ${flight.status}${flight.delayMinutes > 0 ? ` (+${flight.delayMinutes} mins)` : ''}\n🚪 Gate: ${flight.gate}`,
        'card',
        { cardType: 'flight' }
      );

      await botSay(
        `📋 *Select from the list of available cover(s) for your trip*\n\n🛡️ *Cover name: Local Travel Basic*\n🛡 Your trip can be protected against:\n⏰ Major delay\n✅ Cancellation\n💰 Premium: *₦2,500*\n🏢 Provider: *Tangerine Insurance*\n📅 Validity: *Single trip*\n\n━━━━━━━━━━━━━━━━\n\n👑 *Cover name: Local Travel Premium*\n🛡 Your trip can be protected against:\n⏰ Major delay\n✅ Cancellation\n✅ Missed connection cover\n💰 Premium: *₦3,500*\n🏢 Provider: *Tangerine Insurance*\n📅 Validity: *Multi Trip*`,
        'buttons',
        {
          buttons: [
            { id: 'plan_basic',   label: '🛡️ Local Travel Basic — ₦2,500' },
            { id: 'plan_premium', label: '👑 Local Travel Premium — ₦3,500' },
          ],
        },
        500
      );
      setStep(STEPS.BUY_SELECT_PLAN);
    },
    [botSay, setInputMode, setStep, updateUserData, userSay]
  );

  const handleSelectPlan = useCallback(
    async (planId) => {
      const actualPlanId = planId.replace('plan_', '');
      const plan = COVER_PLANS.find((p) => p.id === actualPlanId);
      if (!plan) return;

      updateUserData({ selectedPlan: plan });
      userSay(`${plan.emoji} ${plan.name}`);

      await botSay(
        `${plan.emoji} *${plan.name}* — ₦${plan.price.toLocaleString()}\n\nWith TravelAssist you get:\n✅ policy on WhatsApp\n✅ flight alerts\n✅ support if disruption happens\n\nWhat would you like to do next?`,
        'buttons',
        {
          buttons: [
            { id: 'proceed_kyc',      label: '✅ Continue to KYC' },
            { id: 'ask_question',     label: '❓ Ask a question' },
            { id: 'cancel_purchase',  label: '✗ Cancel purchase' },
          ],
        }
      );
      setStep(STEPS.BUY_CONFIRM);
    },
    [botSay, setStep, updateUserData, userSay]
  );

  const showComparePlans = useCallback(async () => {
    userSay('🔍 Compare Plans');
    await botSay(
      `� *Select from the list of available cover(s) for your trip*\n\n🛡️ *Cover name: Local Travel Basic*\n🛡 Your trip can be protected against:\n⏰ Major delay\n✅ Cancellation\n💰 Premium: *₦2,500*\n🏢 Provider: *Tangerine Insurance*\n📅 Validity: *Single trip*\n\n━━━━━━━━━━━━━━━━\n\n👑 *Cover name: Local Travel Premium*\n🛡 Your trip can be protected against:\n⏰ Major delay\n✅ Cancellation\n✅ Missed connection cover\n💰 Premium: *₦3,500*\n🏢 Provider: *Tangerine Insurance*\n📅 Validity: *Multi Trip*`,
      'buttons',
      {
        buttons: [
          { id: 'plan_basic',   label: '🛡️ Local Travel Basic — ₦2,500' },
          { id: 'plan_premium', label: '👑 Local Travel Premium — ₦3,500' },
        ],
      }
    );
  }, [botSay, userSay]);

  // ─── KYC FLOW ──────────────────────────────────────────────────────────────

  const startKYC = useCallback(async () => {
    if (stateRef.current.userData.kycVerified) {
      await botSay('✅ Your identity is already verified!\n\nProceeding to payment...', 'text', {}, 400);
      await startPaymentFlow();
      return;
    }
    setStep(STEPS.KYC_INTRO);
    await botSay(
      '🪪 For payment, we may need to verify your identity to ensure security and accurate policy issuance. If you\'ve already completed this, we\'ll only ask again if your details have changed.\n\n*How would you like to verify your identity? Select the country that issued your national biometric ID:*',
      'buttons',
      {
        buttons: [
          { id: 'kyc_bvn', label: '🏦 Verify with BVN Nigeria' },
          { id: 'kyc_nin', label: '🆔 Verify with NIN' },
          { id: 'help',    label: '🙋 Help' },
        ],
      }
    );
  }, [botSay, setStep]); // eslint-disable-line

  const handleKYCTypeSelect = useCallback(async (type) => {
    const isNIN = type === 'kyc_nin';
    const label = isNIN ? 'NIN' : 'BVN';
    updateUserData({ kycType: label });
    userSay(`${isNIN ? '🆔' : '🏦'} Verify with ${label}`);
    setStep(STEPS.KYC_CONSENT);
    await botSay(
      `🔒 *We will only use your ${label} to verify your identity for this purchase.*\n\nWe do not store or share your ${label} number.\n\nDo you want to continue?`,
      'buttons',
      {
        buttons: [
          { id: 'kyc_consent_yes', label: '✅ Yes, continue' },
          { id: 'kyc_consent_no',  label: '↩️ Go back' },
        ],
      }
    );
  }, [botSay, setStep, updateUserData, userSay]);

  const handleKYCConsent = useCallback(async (agreed) => {
    if (!agreed) {
      await botSay(
        '⚠️ We need identity verification before payment and policy issuance.',
        'buttons',
        {
          buttons: [
            { id: 'proceed_kyc',     label: '🪪 Continue with KYC' },
            { id: 'cancel_purchase', label: '❌ Cancel purchase' },
          ],
        }
      );
      return;
    }
    const kycType = stateRef.current.userData.kycType;
    const isNIN = kycType === 'NIN';
    setStep(isNIN ? STEPS.KYC_ENTER_NIN : STEPS.KYC_ENTER_BVN);
    await botSay(
      `${isNIN ? '🆔' : '🏦'} Please enter your *11-digit ${kycType}*\n\n📌 Example: *12345678901*`,
      'text'
    );
    setInputMode('text', `Enter ${kycType} (11 digits)`);
  }, [botSay, setInputMode, setStep]);

  const handleKYCValueEntered = useCallback(async (value) => {
    const kycType = stateRef.current.userData.kycType;
    if (!/^\d{11}$/.test(value)) {
      await botSay(
        `⚠️ Please enter an 11-digit BVN or NIN\nExample: 12345678901`,
        'text'
      );
      return;
    }
    setInputMode(null);
    userSay('•'.repeat(value.length));
    updateUserData({ kycValue: value });
    setStep(STEPS.KYC_CONFIRM);
    await botSay(
      `📋 *KYC Summary*\n\n🪪 ID Type: *${kycType}*\n🔑 Number: *${value.slice(0, 3)}•••••${value.slice(-3)}*\n\nShall I proceed with verification?`,
      'summary',
      {
        summaryType: 'kyc',
        buttons: [
          { id: 'confirm_kyc', label: '✅ Verify Identity' },
          { id: 'change_kyc',  label: '🔄 Change Details' },
        ],
      }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const processKYC = useCallback(async () => {
    userSay('✅ Verify Identity');
    setStep(STEPS.KYC_PROCESSING);
    setInputMode(null);
    await botSay('🔍 *Checking your details now...*\n\nPlease wait a moment', 'status', { statusType: 'loading' });

    const { kycType, kycValue } = stateRef.current.userData;
    const result = await simulateKYC(kycType, kycValue);

    if (result.success) {
      dispatch({ type: 'RESET_RETRY', payload: 'kyc' });
      updateUserData({ kycVerified: true, kycName: result.data.name });
      setStep(STEPS.KYC_SUCCESS);
      await botSay(
        `✅ *Identity verified*\n\n👤 Name: *${result.data.name}*\n🟢 Status: Verified\n\nYou can now continue to payment.`,
        'card',
        {
          cardType: 'kyc_success',
          buttons: [
            { id: 'proceed_payment', label: '💳 Continue to payment' },
            { id: 'review_trip',     label: '✏️ Review trip details' },
            { id: 'main_menu',       label: '🏠 Main menu' },
          ],
        }
      );
    } else {
      dispatch({ type: 'INCREMENT_RETRY', payload: 'kyc' });
      const kycAttempts = (stateRef.current.retryCounters.kyc || 0) + 1;
      if (kycAttempts >= 3) {
        dispatch({ type: 'RESET_RETRY', payload: 'kyc' });
        setStep(STEPS.KYC_MAX_RETRIES);
        await botSay(
          `⚠️ We could not verify your details automatically.`,
          'buttons',
          {
            buttons: [
              { id: 'kyc_bvn',         label: '🏦 Try BVN again' },
              { id: 'kyc_nin',         label: '🆔 Try NIN instead' },
              { id: 'contact_support', label: '🙋 Get help' },
            ],
          }
        );
      } else {
        const attemptsLeft = 3 - kycAttempts;
        setStep(STEPS.KYC_FAILED);
        await botSay(
          `⚠️ We could not verify your details automatically.`,
          'buttons',
          {
            buttons: [
              { id: 'kyc_bvn',         label: '🏦 Try BVN again' },
              { id: 'kyc_nin',         label: '🆔 Try NIN instead' },
              { id: 'contact_support', label: '🙋 Get help' },
            ],
          }
        );
      }
    }
  }, [botSay, setInputMode, setStep, updateUserData, userSay]); // eslint-disable-line

  // ─── PAYOUT OPTIONS FLOW (how user receives money) ─────────────────────────

  const startPayoutOptions = useCallback(async () => {
    pushFlowStack(STEPS.MAIN_MENU);
    userSay('💳 Payment options');
    setStep(STEPS.PAYOUT_SELECT);
    await botSay(
      '💰 *Payout options*\n\nChoose how you would like to *receive money* for any future payouts:',
      'buttons',
      {
        buttons: [
          { id: 'payout_bank',   label: '🏦 Bank transfer' },
          { id: 'payout_wallet', label: '👛 Wallet' },
        ],
      }
    );
  }, [botSay, pushFlowStack, setStep, userSay]);

  const handlePayoutMethod = useCallback(async (methodId) => {
    const isBank = methodId === 'payout_bank';
    updateUserData({ payoutMethod: isBank ? 'bank' : 'wallet' });
    userSay(isBank ? '🏦 Bank transfer' : '👛 Wallet');

    if (isBank) {
      setStep(STEPS.PAYOUT_BANK_ACCOUNT);
      await botSay(
        '🏦 Please enter your *10-digit account number* for future payouts:',
        'text'
      );
      setInputMode('text', 'Enter 10-digit account number');
    } else {
      setStep(STEPS.PAYOUT_WALLET_PROVIDER);
      await botSay(
        '👛 *Wallet*\n\nChoose wallet option:',
        'buttons',
        {
          buttons: WALLET_PROVIDERS.map((w) => ({ id: `payout_wallet_${w.id}`, label: `${w.emoji} ${w.label}` })),
        }
      );
    }
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handlePayoutBankAccount = useCallback(async (value) => {
    if (!/^\d{10}$/.test(value.trim())) {
      await botSay('⚠️ Please enter a valid *10-digit account number*.', 'text');
      return;
    }
    userSay('••••••••' + value.slice(-2));
    updateUserData({ payoutBankAccount: value.trim() });
    setInputMode(null);
    setStep(STEPS.PAYOUT_BANK_NAME);
    await botSay(
      '🏦 Please enter at least the *first 3 characters* of your bank name to receive money for future payouts\n\n📌 Examples: *Zen* (Zenith), *Wem* (Wema), *GT* (GTBank)',
      'text'
    );
    setInputMode('text', 'e.g. Zen, Wem, GT');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handlePayoutBankName = useCallback(async (value) => {
    const query = value.trim().toLowerCase();
    const matches = NIGERIAN_BANKS.filter((b) => b.toLowerCase().includes(query));
    if (matches.length === 0) {
      await botSay(
        `⚠️ No banks found matching "*${value}*". Please try again.\n\n📌 Examples: *Zenith*, *GTBank*, *Access*`,
        'text'
      );
      return;
    }
    userSay(value);
    setInputMode(null);
    if (matches.length === 1) {
      updateUserData({ payoutBankName: matches[0] });
      setStep(STEPS.PAYOUT_SAVED);
      await botSay(
        `✅ *Payout details saved!*\n\n🏦 Bank: *${matches[0]}*\n💰 Account: *••••••••${stateRef.current.userData.payoutBankAccount?.slice(-2)}*\n\nWe'll use these details for any future payouts.`,
        'buttons',
        { buttons: [{ id: 'main_menu', label: '🏠 Main Menu' }] }
      );
    } else {
      // Show bank list to select from
      await botSay(
        `🏦 Please select your bank:`,
        'list',
        {
          listType: 'banks',
          items: matches.map((b) => ({ id: `select_bank_${b.replace(/\s/g, '_')}`, label: b })),
          footer: [],
        }
      );
    }
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handlePayoutWalletProvider = useCallback(async (providerId) => {
    const key = providerId.replace('payout_wallet_wallet_', '').replace('payout_wallet_', '');
    const provider = WALLET_PROVIDERS.find((w) => w.id === `wallet_${key}` || w.id === key);
    const label = provider?.label || key;
    updateUserData({ payoutWalletProvider: label });
    userSay(`${provider?.emoji || '👛'} ${label}`);
    setStep(STEPS.PAYOUT_WALLET_PHONE);
    await botSay(
      `📱 Enter the *phone number* linked to your *${label}* wallet\n\n📌 Example: *08012345678*`,
      'text'
    );
    setInputMode('text', 'e.g. 08012345678');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handlePayoutWalletPhone = useCallback(async (value) => {
    if (!/^0[7-9]\d{9}$/.test(value.trim())) {
      await botSay('⚠️ Please enter a valid Nigerian phone number.\n\n📌 Example: *08012345678*', 'text');
      return;
    }
    userSay(value.slice(0, 4) + '•••••' + value.slice(-3));
    updateUserData({ payoutWalletPhone: value.trim() });
    setInputMode(null);
    setStep(STEPS.PAYOUT_SAVED);
    const provider = stateRef.current.userData.payoutWalletProvider;
    await botSay(
      `✅ *Payout details saved!*\n\n👛 Wallet: *${provider}*\n📱 Phone: *${value.slice(0, 4)}•••••${value.slice(-3)}*\n\nWe'll send payouts directly to your wallet.`,
      'buttons',
      { buttons: [{ id: 'main_menu', label: '🏠 Main Menu' }] }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  // ─── PAYMENT FLOW (how user pays premium) ──────────────────────────────────

  const startPaymentFlow = useCallback(async () => {
    setStep(STEPS.PAYMENT_SELECT_METHOD);
    const plan = stateRef.current.userData.selectedPlan;
    const ud = stateRef.current.userData;
    const count = ud.travellerCount || 1;

    await botSay(
      `🔒 *You're one step away from activating your cover*\n\n*Payment Summary*\n✈️ Policy: *${plan?.name || 'Local Travel Basic'}*\n✈️ Flight: *${ud.flightNumber}*\n📅 Date: *${ud.travelDate || '—'}*\n👤 Traveller${count > 1 ? 's' : ''}: *${count}*\n🪪 KYC: *Verified*\n💰 Amount: *₦${plan?.price?.toLocaleString() || '2,500'}*\n\nChoose a payment method:`,
      'buttons',
      {
        buttons: [
          { id: 'pay_bank',   label: '🏦 Bank transfer' },
          { id: 'pay_card',   label: '💳 Card payment' },
          { id: 'pay_wallet', label: '👛 Wallet' },
          { id: 'pay_ussd',   label: '#️⃣ USSD' },
        ],
      },
      500
    );
  }, [botSay, setStep]); // eslint-disable-line

  const startPayment = startPaymentFlow;

  const handlePaymentMethod = useCallback(async (methodId) => {
    const methodMap = {
      pay_card: 'card', pay_bank: 'bank', pay_ussd: 'ussd', pay_wallet: 'wallet',
    };
    const method = methodMap[methodId];
    updateUserData({ paymentMethod: method });
    const plan = stateRef.current.userData.selectedPlan;
    const amount = plan?.price || 2500;
    const ref = `TA${Date.now().toString().slice(-6)}`;

    if (method === 'bank') {
      userSay('🏦 Bank transfer');
      setStep(STEPS.PAYMENT_BANK_DETAILS);
      await botSay(
        `🏦 *Bank Transfer*\n\nPlease transfer *₦${amount.toLocaleString()}* to:\n\nBank: *Example Bank*\nAccount Name: *TravelAssist Payments*\nAccount No: *0123456789*\nReference: *${ref}*\n\n⚠️ Use the reference as your narration.\n\nAfter payment, reply:`,
        'buttons',
        {
          buttons: [
            { id: 'confirm_bank_pay', label: '✅ I have paid' },
            { id: 'check_payment_status', label: '🔄 Refresh payment status' },
          ],
        }
      );
    } else if (method === 'card') {
      userSay('💳 Card payment');
      setStep(STEPS.PAYMENT_CARD_DETAILS);
      await botSay(
        `💳 *Card Payment*\n\nClick the secure payment link below:\n\n👉 *[Pay ₦${amount.toLocaleString()}]*\n\n🔒 Powered by Paystack\n\nAfter payment, we'll confirm your cover here on WhatsApp.`,
        'buttons',
        {
          buttons: [
            { id: 'confirm_card_pay', label: '✅ Pay ₦' + amount.toLocaleString() },
            { id: 'change_payment',   label: '🔄 Change Method' },
          ],
        }
      );
    } else if (method === 'ussd') {
      userSay('#️⃣ USSD');
      setStep(STEPS.PAYMENT_USSD_CODE);
      await botSay(
        `#️⃣ *USSD Payment*\n\nDial the code below to complete payment:\n\n📟 *GTBank:* *737*000*${amount}#\n📟 *Access:* *901*000*${amount}#\n📟 *UBA:* *919*000*${amount}#\n📟 *Zenith:* *966*000*${amount}#\n\n💰 Amount: *₦${amount.toLocaleString()}*\n📝 Reference: *${ref}*\n\nAfter payment, reply:`,
        'buttons',
        {
          buttons: [
            { id: 'confirm_ussd_pay', label: '✅ I have paid' },
            { id: 'check_payment_status', label: '🔄 Refresh payment status' },
          ],
        }
      );
    } else if (method === 'wallet') {
      userSay('👛 Wallet');
      setStep(STEPS.PAYMENT_WALLET_PROVIDER);
      await botSay(
        '👛 *Wallet Payment*\n\nChoose your wallet:',
        'buttons',
        {
          buttons: WALLET_PROVIDERS.map((w) => ({ id: `pay_wallet_${w.id}`, label: `${w.emoji} ${w.label}` })),
        }
      );
    }
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleWalletProviderSelected = useCallback(async (providerId) => {
    const key = providerId.replace('pay_wallet_wallet_', '').replace('pay_wallet_', '');
    const provider = WALLET_PROVIDERS.find((w) => w.id === `wallet_${key}` || w.id === key);
    const label = provider?.label || key;
    updateUserData({ walletProvider: label });
    userSay(`${provider?.emoji || '👛'} ${label}`);
    setStep(STEPS.PAYMENT_WALLET_PHONE);
    await botSay(
      `📱 Enter the phone number linked to your *${label}* wallet\n\n📌 Example: *08012345678*`,
      'text'
    );
    setInputMode('text', 'e.g. 08012345678');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleWalletPhoneEntered = useCallback(async (value) => {
    if (!/^0[7-9]\d{9}$/.test(value.trim())) {
      await botSay('⚠️ Please enter a valid Nigerian phone number.\n\n📌 Example: *08012345678*', 'text');
      return;
    }
    userSay(value.slice(0, 4) + '•••••' + value.slice(-3));
    updateUserData({ walletPhone: value.trim() });
    setInputMode(null);
    setStep(STEPS.PAYMENT_WALLET_PROMPT);
    const plan = stateRef.current.userData.selectedPlan;
    await botSay(
      `📲 *A payment prompt has been sent to your ${stateRef.current.userData.walletProvider} wallet*\n\nPlease approve it on your device\n\n💰 Amount: *₦${plan?.price?.toLocaleString() || '2,500'}*`,
      'buttons',
      {
        buttons: [
          { id: 'confirm_wallet_pay',   label: '✅ I have approved' },
          { id: 'check_payment_status', label: '🔄 Refresh payment status' },
        ],
      }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const processPayment = useCallback(async (confirmId) => {
    const labelMap = {
      confirm_card_pay:   '✅ Paying with Card...',
      confirm_bank_pay:   '✅ Confirming Transfer...',
      confirm_ussd_pay:   '✅ Confirming USSD...',
      confirm_wallet_pay: '✅ Approving Wallet...',
    };
    userSay(labelMap[confirmId] || '✅ Confirming...');
    setStep(STEPS.PAYMENT_PROCESSING);
    const method = stateRef.current.userData.paymentMethod;
    const plan = stateRef.current.userData.selectedPlan;
    const ud = stateRef.current.userData;

    await botSay('⏳ *Processing your payment...*\n\nPlease wait.', 'status', { statusType: 'loading' });
    const result = await simulatePayment(method, plan?.price);

    if (result.success) {
      const policyNumber = generatePolicyNumber();
      updateUserData({ policyNumber });
      dispatch({ type: 'RESET_RETRY', payload: 'payment' });
      setStep(STEPS.PAYMENT_SUCCESS);

      await botSay(
        `✅ *Payment successful*\n\nYour cover is now active 🛡️`,
        'status', { statusType: 'success' }
      );

      const travellers = ud.travellerNames?.length > 0 ? ud.travellerNames.join(', ') : (ud.kycName || 'Traveller');
      await botSay(
        `📄 *Policy No: ${policyNumber}*\n✈️ Flight: *${ud.flightNumber}*\n📅 Date: *${ud.travelDate || '—'}*\nTraveller Name: *${travellers}*\n\nGot your boarding pass handy? You can upload it now 👍\n\nIf not, no worries — you can upload it later. We'll just need it before any payout\n\nWhat would you like to do next?`,
        'card',
        {
          cardType: 'policy_issued',
          buttons: [
            { id: 'enable_alerts',  label: '🔔 Turn on flight alerts' },
            { id: 'view_policy',    label: '📄 View my policy document' },
            { id: 'upload_boarding',label: '🛂 Upload boarding pass' },
            { id: 'main_menu',      label: '🏠 Main menu' },
          ],
        }
      );
    } else if (result.status === 'PENDING') {
      dispatch({ type: 'RESET_RETRY', payload: 'payment' });
      setStep(STEPS.PAYMENT_PENDING);
      await botSay(
        `⏳ We haven't confirmed your payment yet\nPlease wait a little and try again`,
        'buttons',
        {
          buttons: [
            { id: 'check_payment_status', label: '🔄 Refresh payment status' },
            { id: 'help',                 label: '🙋 Help' },
          ],
        }
      );
    } else {
      dispatch({ type: 'INCREMENT_RETRY', payload: 'payment' });
      const payAttempts = (stateRef.current.retryCounters.payment || 0) + 1;
      if (payAttempts >= 2) {
        dispatch({ type: 'RESET_RETRY', payload: 'payment' });
        setStep(STEPS.PAYMENT_MAX_RETRIES);
        await botSay(
          `❌ Payment was not successful\nPlease choose what to do next:`,
          'buttons',
          {
            buttons: [
              { id: 'pay_card',        label: '💳 Try again' },
              { id: 'pay_bank',        label: '🏦 Use bank transfer' },
              { id: 'pay_ussd',        label: '#️⃣ Use USSD' },
              { id: 'contact_support', label: '🙋 Get help' },
            ],
          }
        );
      } else {
        setStep(STEPS.PAYMENT_FAILED);
        await botSay(
          `❌ Payment was not successful\nPlease choose what to do next:`,
          'buttons',
          {
            buttons: [
              { id: 'retry_payment',   label: '💳 Try again' },
              { id: 'change_payment',  label: '🏦 Use bank transfer' },
              { id: 'pay_ussd',        label: '#️⃣ Use USSD' },
              { id: 'contact_support', label: '🙋 Get help' },
            ],
          }
        );
      }
    }
  }, [botSay, setStep, updateUserData, userSay]); // eslint-disable-line

  // ─── POLICY FLOW ───────────────────────────────────────────────────────────

  const showPolicies = useCallback(async () => {
    pushFlowStack(STEPS.MAIN_MENU);
    userSay('📋 My policies');
    setStep(STEPS.POLICY_LIST);

    // Spec: 3 lookup methods — phone / policy number / flight number
    await botSay(
      '� *Check my policy*\n\nHow would you like to find your policy?',
      'buttons',
      {
        buttons: [
          { id: 'lookup_phone',  label: '📱 Use my phone number' },
          { id: 'lookup_policy', label: '🔢 Enter policy number' },
          { id: 'lookup_flight', label: '✈️ Search by flight number' },
        ],
      }
    );
  }, [botSay, pushFlowStack, setStep, userSay]);

  const handlePolicyLookupMethod = useCallback(async (methodId) => {
    const prompts = {
      lookup_policy: { label: '🔢 Policy number', hint: 'e.g. TA-2024-001', step: STEPS.POLICY_LOOKUP },
      lookup_flight: { label: '✈️ Flight number', hint: 'e.g. AA123',       step: STEPS.POLICY_LOOKUP },
    };

    if (methodId === 'lookup_phone') {
      userSay('📱 Use my phone number');
      updateUserData({ policyLookupMethod: 'lookup_phone' });
      setStep(STEPS.POLICY_LOOKUP);
      await botSay(
        '📱 We\'ll check for active policies linked to this WhatsApp number',
        'buttons',
        {
          buttons: [
            { id: 'lookup_phone_confirm', label: '✅ Continue' },
            { id: 'check_policy',         label: '↩️ Back' },
          ],
        }
      );
      return;
    }

    const cfg = prompts[methodId];
    if (!cfg) return;
    userSay(cfg.label);
    updateUserData({ policyLookupMethod: methodId });
    setStep(cfg.step);
    await botSay(
      `🔍 Please enter your *${cfg.label.replace(/^[^\s]+ /, '')}*:`,
      'text'
    );
    setInputMode('text', cfg.hint);
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handlePolicyLookupValue = useCallback(async (value) => {
    userSay(value);
    setInputMode(null);
    await botSay('⏳ Looking up your policy...', 'status', { statusType: 'loading' }, 400);
    await new Promise((r) => setTimeout(r, 1000));

    const method = stateRef.current.userData.policyLookupMethod;
    let matches = [];
    if (method === 'lookup_phone') {
      matches = MOCK_POLICIES.filter((p) => p.phone === value.trim() || p.phone === stateRef.current.userData.phone);
      if (matches.length === 0) matches = MOCK_POLICIES; // demo: show all for any phone
    } else if (method === 'lookup_policy') {
      matches = MOCK_POLICIES.filter((p) => p.policyNumber.toLowerCase() === value.trim().toLowerCase());
    } else if (method === 'lookup_flight') {
      matches = MOCK_POLICIES.filter((p) => p.flightNumber.toUpperCase() === value.trim().toUpperCase());
      if (matches.length === 0) matches = MOCK_POLICIES; // demo fallback
    }

    if (matches.length === 0) {
      setStep(STEPS.POLICY_NOT_FOUND);
      await botSay(
        `⚠️ We couldn't find an active policy linked to this number`,
        'buttons',
        {
          buttons: [
            { id: 'buy_cover',     label: '✈️ Buy cover' },
            { id: 'lookup_policy', label: '🔢 Enter policy number' },
            { id: 'help',         label: '🙋 Help' },
          ],
        }
      );
      return;
    }

    setStep(STEPS.POLICY_LIST);
    await botSay(
      `📋 *${matches.length === 1 ? 'Your policy' : `${matches.length} policies found`}*\n\nSelect a policy to view details:`,
      'list',
      {
        listType: 'policies',
        items: MOCK_POLICIES.map((p, i) => ({
          id: `policy_${i}`,
          label: `${p.status === 'ACTIVE' ? '🟢' : '🔴'} ${p.policyNumber}`,
          subtitle: `${p.plan} · ${p.flightNumber}`,
        })),
        footer: [{ id: 'main_menu', label: '🏠 Main menu' }],
      }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]); // eslint-disable-line

  const showPolicyDetail = useCallback(
    async (index) => {
      const policy = MOCK_POLICIES[index];
      if (!policy) return;

      const statusEmoji = policy.status === 'ACTIVE' ? '✅' : '🔴';
      await botSay(
        `📄 *Your Policy Details*

Policy No: *${policy.policyNumber}*
Status: *${policy.status === 'ACTIVE' ? 'Active' : policy.status}* ${statusEmoji}
Airline: *${policy.airline || policy.plan}*
Flight: *${policy.flightNumber}*
Date: *${policy.travelDate || policy.issueDate}*`,
        'card',
        {
          cardType: 'policy_detail',
          buttons: [
            { id: `download_policy_${index}`, label: '📥 Download policy' },
            { id: 'manage_alerts',            label: '🔔 Manage alerts' },
            { id: 'upload_boarding',          label: '🛂 Upload boarding pass' },
            { id: 'help',                     label: '🙋 Help' },
          ],
        }
      );
      setStep(STEPS.POLICY_DETAIL);
    },
    [botSay, setStep]
  );

  const handleCancelPolicy = useCallback(
    async (index) => {
      const policy = MOCK_POLICIES[index];
      if (!policy) return;

      setStep(STEPS.POLICY_CANCEL_CONFIRM);
      await botSay(
        `🚫 *Cancel policy?*\n\n🎫 ${policy.policyNumber}\n✈️ Flight: *${policy.flightNumber}*\n🛡️ Plan: *${policy.plan}*\n\n⚠️ Cancelling will remove your cover. Any refund will be processed within *5-7 business days*.`,
        'buttons',
        {
          buttons: [
            { id: `confirm_cancel_${index}`, label: '✅ Yes, cancel policy' },
            { id: `policy_${index}`,          label: '⬅️ Keep my policy' },
          ],
        }
      );
    },
    [botSay, setStep]
  );

  const confirmCancelPolicy = useCallback(
    async (index) => {
      const policy = MOCK_POLICIES[index];
      if (!policy) return;

      userSay('✅ Yes, cancel policy');
      await botSay('⏳ Cancelling your policy...', 'status', { statusType: 'loading' }, 400);
      await new Promise((r) => setTimeout(r, 1500));

      MOCK_POLICIES[index] = { ...policy, status: 'CANCELLED' };

      setStep(STEPS.POLICY_CANCELLED);
      await botSay(
        `✅ *Policy cancelled*\n\n🎫 ${policy.policyNumber}\n🔴 Status: *CANCELLED*\n\n💰 Refund of *₦${(parseInt(policy.coverAmount.replace(/[₦,]/g, '')) * 0.8 || 0).toLocaleString()}* will be processed in *5-7 business days*.`,
        'buttons',
        {
          buttons: [
            { id: 'check_policy', label: '📋 My Policies' },
            { id: 'main_menu', label: '🏠 Main Menu' },
          ],
        }
      );
    },
    [botSay, setStep, userSay]
  );

  // ─── BOARDING PASS FLOW (fallback / post-purchase only) ───────────────────

  const startBoardingUpload = useCallback(async () => {
    userSay('🛂 Upload boarding pass');
    const hasActivePolicy = MOCK_POLICIES.some((p) => p.status === 'ACTIVE');
    if (!hasActivePolicy) {
      setStep(STEPS.BOARDING_NO_POLICY);
      await botSay(
        `⚠️ *No active policy found*\n\nYou need an active travel cover policy before uploading a boarding pass.`,
        'buttons',
        {
          buttons: [
            { id: 'buy_cover',    label: '🛡️ Buy cover' },
            { id: 'check_policy', label: '📋 My policies' },
            { id: 'main_menu',    label: '🏠 Main menu' },
          ],
        }
      );
      return;
    }

    setStep(STEPS.BOARDING_UPLOAD);
    await botSay(
      '🛂 *Upload boarding pass*\n\nPlease choose an option:',
      'buttons',
      {
        buttons: [
          { id: 'boarding_upload_start', label: '📄 Upload for your policy' },
          { id: 'help',                  label: '🙋 Help' },
        ],
      }
    );
  }, [botSay, setStep, userSay]);

  const startBoardingUploadPrompt = useCallback(async () => {
    setStep(STEPS.BOARDING_UPLOAD);
    await botSay(
      `📎 *Please upload a clear image or PDF of your boarding pass*\n\nAccepted formats: *JPEG, PDF, GIF, TIFF, PNG*\nMaximum size: *20 MB*\n\nMake sure we can see:\n✅ passenger name or names\n✅ booking reference\n✅ airport details\n✅ flight number\n✅ travel date\n\nType *0* to go back`,
      'buttons',
      { buttons: [{ id: 'trigger_file_upload', label: '📎 Choose file to upload' }] }
    );
  }, [botSay, setStep]);

  const handleBoardingPassUploaded = useCallback(
    async (fileName) => {
      setInputMode(null);
      const ext = fileName.split('.').pop().toLowerCase();
      if (!['pdf', 'jpg', 'jpeg', 'png', 'gif', 'tiff', 'tif'].includes(ext)) {
        setStep(STEPS.BOARDING_BAD_FILE);
        await botSay(
          `❌ *Unsupported file type*\n\nThe file *${fileName}* is not accepted.\n\n✅ Accepted: *JPEG, PDF, GIF, TIFF, PNG*`,
          'buttons',
          { buttons: [{ id: 'trigger_file_upload', label: '📎 Upload again' }, { id: 'help', label: '🙋 Help' }] }
        );
        return;
      }
      setStep(STEPS.BOARDING_PROCESSING);
      await botSay(`✅ File received: *${fileName}*\n\n⏳ Verifying...`, 'status', { statusType: 'loading' });

      const result = await simulateBoardingPassVerification(fileName);
      await new Promise((r) => setTimeout(r, 1800));

      if (result.success) {
        updateUserData({ boardingPassUploaded: true });
        setStep(STEPS.BOARDING_SUCCESS);
        await botSay(
          `✅ *Boarding pass upload confirmed*\n\nBoarding pass information:\n✈️ Flight: *${result.flightNumber}*\n📅 Date: *${result.date}*\n\nWhat would you like to do next?`,
          'card',
          {
            cardType: 'boarding_success',
            buttons: [
              { id: 'boarding_link_policy', label: '🔗 Link to my policy' },
              { id: 'check_eligibility',    label: '✅ Check eligibility' },
              { id: 'main_menu',            label: '🏠 Main menu' },
              { id: '99',                   label: '✗ Cancel/Exit' },
            ],
          }
        );
      } else {
        setStep(STEPS.BOARDING_FAILED);
        await botSay(
          `⚠️ We couldn't read the boarding pass clearly\nPlease upload a clearer image showing:\n✅ name\n✅ flight number\n✅ date`,
          'buttons',
          {
            buttons: [
              { id: 'trigger_file_upload', label: '📎 Upload again' },
              { id: 'help',                label: '🙋 Help' },
            ],
          }
        );
      }
    },
    [botSay, setInputMode, setStep, updateUserData]
  );

  // ─── LINK FLIGHT FLOW (NEW) ────────────────────────────────────────────────

  const startLinkFlight = useCallback(async () => {
    pushFlowStack(STEPS.MAIN_MENU);
    userSay('✈️ Link a flight');
    setStep(STEPS.LINK_SELECT_POLICY);

    await botSay('⏳ Loading your policies...', 'status', { statusType: 'loading' }, 300);
    const active = MOCK_POLICIES.filter((p) => p.status === 'ACTIVE');

    if (active.length === 0) {
      await botSay(
        '⚠️ *No active policies*\n\nYou need an active policy to link a flight to.',
        'buttons',
        {
          buttons: [
            { id: 'buy_cover', label: '🛡️ Buy cover' },
            { id: 'main_menu', label: '🏠 Main menu' },
          ],
        }
      );
      return;
    }

    await botSay(
      '✈️ *Link a flight*\n\nSelect the policy you\'d like to link a flight to:',
      'list',
      {
        listType: 'policies',
        items: active.map((p, i) => ({
          id: `link_flight_policy_${MOCK_POLICIES.indexOf(p)}`,
          label: `${p.policyNumber}`,
          subtitle: `${p.plan} · ${p.flightNumber}`,
        })),
        footer: [{ id: 'main_menu', label: '🏠 Main menu' }],
      }
    );
  }, [botSay, pushFlowStack, setStep, userSay]);

  const handleLinkPolicySelect = useCallback(async (buttonId) => {
    const idx = parseInt(buttonId.replace('link_flight_policy_', ''));
    const policy = MOCK_POLICIES[idx];
    if (!policy) return;
    updateUserData({ linkingPolicyIndex: idx, linkingPolicy: policy });
    userSay(policy.policyNumber);
    setStep(STEPS.LINK_ENTER_FLIGHT);
    await botSay(
      `✈️ Enter the *flight number* you'd like to link to policy *${policy.policyNumber}*\n\n📌 Example: *P47123*, *AA123*`,
      'text'
    );
    setInputMode('text', 'e.g. P47123');
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const handleLinkFlightEntered = useCallback(async (value) => {
    const fn = value.trim().toUpperCase();
    if (!/^[A-Z0-9]{4,7}$/.test(fn)) {
      await botSay(
        `⚠️ "*${value}*" doesn't look like a valid flight number.\n\n📌 Example: *P47123*, *AA123*`,
        'text'
      );
      return;
    }
    userSay(fn);
    setInputMode(null);
    await botSay('⏳ Looking up flight...', 'status', { statusType: 'loading' }, 300);
    const result = await simulateFlightLookup(fn);
    const policy = stateRef.current.userData.linkingPolicy;

    if (!result.success) {
      setStep(STEPS.LINK_ENTER_FLIGHT);
      await botSay(
        `❌ Flight *${fn}* not found.\n\nPlease check and try again.`,
        'buttons',
        { buttons: [{ id: 'retry_link_flight', label: '🔄 Try again' }, { id: 'main_menu', label: '🏠 Cancel' }] }
      );
      return;
    }

    const f = result.data;
    updateUserData({ linkFlightData: f, linkFlightNumber: fn });
    setStep(STEPS.LINK_CONFIRM);
    await botSay(
      `✈️ *Confirm flight link*\n\n📄 Policy: *${policy.policyNumber}*\n✈️ Flight: *${fn}* — ${f.airline}\n📍 ${f.origin} → ${f.destination}\n📅 ${f.date}, ${f.scheduledDeparture}\n\nWould you like to link this flight to your policy?`,
      'buttons',
      {
        buttons: [
          { id: 'confirm_link_flight', label: '✅ Yes, link this flight' },
          { id: 'link_flight',         label: '🔄 Use a different flight' },
          { id: 'main_menu',           label: '❌ Cancel' },
        ],
      }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  const confirmLinkFlight = useCallback(async () => {
    userSay('✅ Yes, link this flight');
    const ud = stateRef.current.userData;
    const idx = ud.linkingPolicyIndex;
    const fn = ud.linkFlightNumber;
    if (idx == null || !fn) return;

    await botSay('⏳ Linking flight...', 'status', { statusType: 'loading' }, 400);
    await new Promise((r) => setTimeout(r, 1200));
    MOCK_POLICIES[idx] = { ...MOCK_POLICIES[idx], linkedFlight: fn };

    setStep(STEPS.LINK_SUCCESS);
    await botSay(
      `✅ *Flight linked!*\n\n✈️ *${fn}* is now linked to policy *${MOCK_POLICIES[idx].policyNumber}*\n\n🔔 We'll monitor this flight and alert you automatically if there's a disruption.`,
      'buttons',
      {
        buttons: [
          { id: 'check_policy', label: '📋 View policy' },
          { id: 'main_menu',    label: '🏠 Main menu' },
        ],
      }
    );
  }, [botSay, setStep, userSay]);

  // ─── UPDATE DETAILS FLOW (NEW) ─────────────────────────────────────────────

  const startUpdateDetails = useCallback(async () => {
    pushFlowStack(STEPS.MAIN_MENU);
    userSay('✏️ Update details');
    setStep(STEPS.UPDATE_MENU);
    await botSay(
      '✏️ *Update your details*\n\nWhat would you like to update?',
      'buttons',
      {
        buttons: [
          { id: 'update_name',  label: '👤 Name' },
          { id: 'update_email', label: '📧 Email address' },
          { id: 'update_phone', label: '📱 Phone number' },
          { id: 'update_bank',  label: '🏦 Bank/payout details' },
          { id: 'main_menu',    label: '🏠 Main menu' },
        ],
      }
    );
  }, [botSay, pushFlowStack, setStep, userSay]);

  const handleUpdateField = useCallback(async (fieldId) => {
    const config = {
      update_name:  { step: STEPS.UPDATE_NAME,  label: '👤 Full name',       hint: 'Enter your full name' },
      update_email: { step: STEPS.UPDATE_EMAIL, label: '📧 Email address',    hint: 'e.g. you@example.com' },
      update_phone: { step: STEPS.UPDATE_PHONE, label: '📱 Phone number',     hint: 'e.g. 08012345678' },
      update_bank:  { step: STEPS.UPDATE_BANK,  label: '🏦 Account number',   hint: 'Enter 10-digit account number' },
    };
    const cfg = config[fieldId];
    if (!cfg) return;
    userSay(cfg.label);
    setStep(cfg.step);
    await botSay(`Please enter your new *${cfg.label.replace(/^[^\s]+ /, '')}*:`, 'text');
    setInputMode('text', cfg.hint);
  }, [botSay, setInputMode, setStep, userSay]);

  const handleUpdateValue = useCallback(async (value) => {
    const step = stateRef.current.currentStep;
    const fieldMap = {
      [STEPS.UPDATE_NAME]:  { key: 'kycName',          label: 'Name' },
      [STEPS.UPDATE_EMAIL]: { key: 'email',             label: 'Email' },
      [STEPS.UPDATE_PHONE]: { key: 'phone',             label: 'Phone number' },
      [STEPS.UPDATE_BANK]:  { key: 'payoutBankAccount', label: 'Account number' },
    };
    const cfg = fieldMap[step];
    if (!cfg) return;

    if (step === STEPS.UPDATE_PHONE && !/^0[7-9]\d{9}$/.test(value.trim())) {
      await botSay('⚠️ Please enter a valid Nigerian phone number (e.g. 08012345678).', 'text');
      return;
    }
    if (step === STEPS.UPDATE_EMAIL && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())) {
      await botSay('⚠️ Please enter a valid email address.', 'text');
      return;
    }

    userSay(step === STEPS.UPDATE_BANK ? '••••••••' + value.slice(-2) : value);
    updateUserData({ [cfg.key]: value.trim() });
    setInputMode(null);
    setStep(STEPS.UPDATE_CONFIRMED);
    await botSay(
      `✅ *${cfg.label} updated successfully*\n\nIs there anything else you'd like to update?`,
      'buttons',
      {
        buttons: [
          { id: 'update_details', label: '✏️ Update something else' },
          { id: 'main_menu',      label: '🏠 Main menu' },
        ],
      }
    );
  }, [botSay, setInputMode, setStep, updateUserData, userSay]);

  // ─── HELP FLOW ─────────────────────────────────────────────────────────────

  const showHelp = useCallback(async () => {
    pushFlowStack(stateRef.current.currentStep);
    userSay('❓ Help');
    setStep(STEPS.HELP_MENU);
    await botSay(
      '❓ *Help*\n\nWhat do you need help with?',
      'list',
      {
        listType: 'help',
        items: FAQ_ITEMS.map((f) => ({ id: `faq_${f.id}`, label: f.question })),
        footer: [
          { id: 'main_menu', label: '🏠 Main menu' },
        ],
      }
    );
  }, [botSay, pushFlowStack, setStep, userSay]);

  const showFAQAnswer = useCallback(async (faqId) => {
    const actualId = faqId.replace('faq_', '');
    const faq = FAQ_ITEMS.find((f) => f.id === actualId);
    if (!faq) return;
    userSay(faq.question);
    await botSay(
      `❓ ${faq.answer}`,
      'buttons',
      {
        buttons: [
          { id: 'back_to_help', label: '⬅️ Back to help' },
          { id: 'main_menu',    label: '🏠 Main menu' },
        ],
      }
    );
  }, [botSay, userSay]);

  // ─── PROACTIVE ALERT PIPELINE (5-stage auto payout) ───────────────────────

  const triggerProactiveAlert = useCallback(async (alertType) => {
    if (alertType === 'disruption_detected') {
      // Stage 1 — Disruption detected
      await botSay(
        `⚠️ *Flight disruption detected*\n\n✈️ Your flight *P47123* (Air Peace) has been delayed by *90 minutes*\n📅 New departure: *13:30*\n\n🛡️ Good news — you have active TravelAssist cover!\n\nWe're checking your eligibility for a payout now...`,
        'status', { statusType: 'loading' }, 500
      );
    } else if (alertType === 'threshold_reached') {
      // Stage 2 — Threshold reached
      await botSay(
        `📊 *Payout threshold reached*\n\nYour delay of *90 minutes* has crossed the *60-minute threshold*\n\n💰 Estimated payout: *₦25,000*\n\nVerifying your eligibility...`,
        'status', { statusType: 'loading' }, 300
      );
    } else if (alertType === 'eligibility_confirmed') {
      // Stage 3 — Eligibility confirmed
      await botSay(
        `✅ *You're eligible for a payout!*\n\n🎫 Policy: *TA-2024-001*\n✈️ Flight: *P47123* (90-min delay)\n💰 Payout amount: *₦25,000*\n\nInitiating transfer to your account...`,
        'status', { statusType: 'loading' }, 300
      );
    } else if (alertType === 'payout_initiated') {
      // Stage 4 — Payout initiated
      await botSay(
        `🏦 *Payout initiated*\n\n💰 ₦25,000 is on its way to your registered account\n⏳ Processing time: *2–4 hours*\n\nYou'll receive a confirmation once the transfer is complete.`,
        'status', { statusType: 'loading' }, 300
      );
    } else if (alertType === 'payout_completed' || alertType === 'payout') {
      // Stage 5 — Complete
      await botSay(
        `🎉 *Payout complete!*\n\n✅ *₦25,000* has been transferred to your account\n🕐 Transferred: just now\n\nThank you for flying with TravelAssist cover. ✈️`,
        'card',
        {
          cardType: 'payout_success',
          buttons: [
            { id: 'check_policy', label: '📋 View my policy' },
            { id: 'main_menu',    label: '🏠 Main menu' },
          ],
        },
        300
      );
    } else if (alertType === 'delay') {
      // Legacy shortcut — triggers full pipeline with delays
      await triggerProactiveAlert('disruption_detected');
      await new Promise((r) => setTimeout(r, 2500));
      await triggerProactiveAlert('threshold_reached');
      await new Promise((r) => setTimeout(r, 2000));
      await triggerProactiveAlert('eligibility_confirmed');
      await new Promise((r) => setTimeout(r, 1800));
      await triggerProactiveAlert('payout_initiated');
      await new Promise((r) => setTimeout(r, 3000));
      await triggerProactiveAlert('payout_completed');
    } else if (alertType === 'flight_delay') {
      // Spec section 4.9 — Flight delay alert
      const ud = stateRef.current.userData;
      await botSay(
        `⚠️ *Flight Alert*\nYour flight *${ud.flightNumber || 'P47123'}* has been delayed ⏰\nNew departure time: *6:45 PM*\n\nWhat would you like to do?`,
        'buttons',
        {
          buttons: [
            { id: 'check_eligibility', label: '🧾 Check eligibility' },
            { id: 'upload_boarding',   label: '🛂 Upload boarding pass' },
            { id: 'help',              label: '🙋 Get help' },
          ],
        },
        300
      );
    } else if (alertType === 'policy_issued') {
      const policyNo = generatePolicyNumber();
      const ud = stateRef.current.userData;
      await botSay(
        `✅ *Policy Issued*\nYour TravelAssist cover is active\n📄 Policy No: *${policyNo}*\n✈️ Flight: *${ud.flightNumber || 'P47123'}*\n📅 Date: *${ud.travelDate || '12 April 2026'}*`,

        'card',
        {
          cardType: 'policy_issued',
          buttons: [
            { id: 'view_policy',   label: '📥 Download policy' },
            { id: 'enable_alerts', label: '🔔 Turn on alerts' },
            { id: 'main_menu',     label: '🏠 Main menu' },
          ],
        },
        300
      );
    }
  }, [botSay, generatePolicyNumber]); // eslint-disable-line

  // ─── GLOBAL BUTTON HANDLER ─────────────────────────────────────────────────

  const handleButtonClick = useCallback(async (buttonId) => {
    const isAbort = ['99', 'cancel', 'main_menu', '00', '0', 'back', '9'].includes(buttonId);
    if (processingRef.current && !isAbort) return;
    processingRef.current = true;
    try {
    // Bootstrap session on first interaction from landing screen
    if (!stateRef.current.sessionStarted) {
      dispatch({ type: 'SET_SESSION_STARTED' });
    }

    // 99 = Cancel
    if (buttonId === '99' || buttonId === 'cancel') {
      dispatch({ type: 'RESET' });
      userSay('99');
      await botSay('❌ Cancelled. Here\'s what you can do:', 'text');
      await showMainMenu(true);
      return;
    }

    // Global navigation
    if (buttonId === 'main_menu' || buttonId === '00') {
      dispatch({ type: 'RESET' });
      await showMainMenu(true);
      return;
    }
    if (buttonId === 'help') { await showHelp(); return; }
    if (buttonId === '9') { dispatch({ type: 'RESET' }); await showMainMenu(true); return; }
    if (buttonId === 'back' || buttonId === '0') {
      dispatch({ type: 'POP_FLOW_STACK' });
      await showMainMenu(true);
      return;
    }

    // ── Buy cover ────────────────────────────────────────────────────────────
    if (buttonId === 'buy_cover') { await startBuyCover(); return; }
    if (buttonId === 'cover_solo') { await handleCoverType('cover_solo'); return; }
    if (buttonId === 'cover_group') { await handleCoverType('cover_group'); return; }
    if (buttonId === 'add_more_travellers') {
      // Re-ask for next traveller name
      const ud = stateRef.current.userData;
      const count = ud.travellerCount || 2;
      const names = ud.travellerNames || [];
      const nextIndex = names.length + 1;
      if (nextIndex <= count) {
        setStep(STEPS.BUY_TRAVELLER_NAMES);
        await botSay(`👤 Traveller ${nextIndex} of ${count} — Please enter their full name:`, 'text');
        setInputMode('text', 'Enter full name');
      }
      return;
    }
    if (buttonId === 'trip_oneway' || buttonId === 'trip_return') {
      await handleTripType(buttonId); return;
    }
    if (buttonId === 'confirm_trip') {
      userSay('✅ Confirm trip');
      const fn = stateRef.current.userData.flightNumber;
      if (fn) { await handleFlightLookup(fn); } else { await startBuyCover(); }
      return;
    }
    if (buttonId === 'edit_trip') { await startBuyCover(); return; }
    if (buttonId === 'review_trip') {
      userSay('✏️ Review trip details');
      const ud = stateRef.current.userData;
      const count = ud.travellerCount || 1;
      const depLabel = ud.departAirport ? ud.departAirport.city : '—';
      const arrLabel = ud.arriveAirport ? ud.arriveAirport.city : '—';
      setStep(STEPS.BUY_TRIP_SUMMARY);
      await botSay(
        `📍 *Trip Summary*\n\n✈️ Airline: *${ud.carrier || '—'}*\n🛫 Route: *${depLabel} → ${arrLabel}*\n✈️ Flight: *${ud.flightNumber || '—'}*\n📅 Date: *${ud.travelDate || '—'}*\n⏰ Departure: *${ud.departTime || '—'}*\n⏰ Arrival: *${ud.arriveTime || '—'}*\n🎫 Booking ref: *${ud.bookingRef || '—'}*\n📍 Trip type: *${ud.tripType === 'return' ? 'Return ↩️' : 'One-way ➡️'}*\n👤 Traveller${count > 1 ? 's' : ''}: *${count}*\n📧 Email: *${ud.email || '—'}*\n\nPlease confirm:`,
        'buttons',
        {
          buttons: [
            { id: 'proceed_kyc', label: '✅ Back to KYC' },
            { id: 'edit_trip',   label: '✏️ Edit trip details' },
          ],
        }
      );
      return;
    }
    if (buttonId === 'retry_flight_lookup' || buttonId === 'retry_flight') {
      setStep(STEPS.BUY_ENTER_FLIGHT);
      await botSay('✈️ Please re-enter your flight number:', 'text');
      setInputMode('text', 'e.g. P47123');
      return;
    }
    if (buttonId.startsWith('airport_depart_')) {
      const code = buttonId.replace('airport_depart_', '');
      await handleDepartAirportSelect(code); return;
    }
    if (buttonId.startsWith('airport_arrive_')) {
      const code = buttonId.replace('airport_arrive_', '');
      await handleArriveAirportSelect(code); return;
    }
    if (buttonId === 'compare_plans') { await showComparePlans(); return; }
    if (buttonId.startsWith('plan_')) { await handleSelectPlan(buttonId); return; }
    if (buttonId === 'change_plan') {
      await botSay('🛡️ *Choose your cover plan:*', 'buttons', {
        buttons: [
          { id: 'plan_basic',   label: '🛡️ Local Travel Basic — ₦2,500' },
          { id: 'plan_premium', label: '👑 Local Travel Premium — ₦3,500' },
        ],
      });
      setStep(STEPS.BUY_SELECT_PLAN);
      return;
    }
    if (buttonId === 'cancel_purchase') { dispatch({ type: 'RESET' }); await showMainMenu(true); return; }
    if (buttonId === 'ask_question') { await showHelp(); return; }
    if (buttonId === 'proceed_kyc') { await startKYC(); return; }

    // ── KYC ──────────────────────────────────────────────────────────────────
    if (buttonId === 'kyc_bvn' || buttonId === 'kyc_nin') { await handleKYCTypeSelect(buttonId); return; }
    if (buttonId === 'kyc_consent_yes') { await handleKYCConsent(true); return; }
    if (buttonId === 'kyc_consent_no')  { await handleKYCConsent(false); return; }
    if (buttonId === 'confirm_kyc') { await processKYC(); return; }
    if (buttonId === 'retry_kyc' || buttonId === 'change_kyc' || buttonId === 'change_kyc_type') {
      await startKYC(); return;
    }
    if (buttonId === 'proceed_payment') { await startPaymentFlow(); return; }

    // ── Payment ──────────────────────────────────────────────────────────────
    if (buttonId === 'change_payment' || buttonId === 'retry_payment') { await startPaymentFlow(); return; }
    if (buttonId.startsWith('pay_wallet_')) { await handleWalletProviderSelected(buttonId); return; }
    if (buttonId.startsWith('pay_')) { await handlePaymentMethod(buttonId); return; }
    if (buttonId.startsWith('confirm_') && buttonId.endsWith('_pay')) { await processPayment(buttonId); return; }
    if (buttonId === 'check_payment_status') {
      await botSay('⏳ Checking payment status...', 'status', { statusType: 'loading' }, 300);
      await new Promise((r) => setTimeout(r, 1500));
      const pol = generatePolicyNumber();
      updateUserData({ policyNumber: pol });
      const plan = stateRef.current.userData.selectedPlan;
      await botSay('✅ Payment confirmed! Issuing your policy...', 'status', { statusType: 'success' });
      await botSay(
        `📄 *Policy issued!*\n\n🎫 Policy No: *${pol}*\n✈️ Flight: *${stateRef.current.userData.flightNumber}*\n🛡️ Plan: *${plan?.name || 'Standard'}*`,
        'card',
        { cardType: 'policy_issued', buttons: [{ id: 'main_menu', label: '🏠 Main menu' }] }
      );
      return;
    }
    if (buttonId === 'manage_alerts' || buttonId === 'enable_alerts') {
      userSay('🔔 Turn on flight alerts');
      await botSay(
        `🔔 *Flight alerts enabled*\n\nWe'll automatically monitor your flight and notify you of any disruptions. If you're eligible for a payout, we'll process it automatically — no action needed from you.`,
        'buttons',
        { buttons: [{ id: 'main_menu', label: '🏠 Main menu' }] }
      );
      return;
    }
    if (buttonId === 'boarding_link_policy') {
      userSay('📄 Link to my policy');
      await startLinkFlight();
      return;
    }
    if (buttonId === 'check_eligibility') {
      userSay('🧾 Check eligibility');
      await botSay(
        `⏳ Your case needs further review`,
        'buttons',
        {
          buttons: [
            { id: 'contact_support', label: '🙋 Speak to support' },
            { id: 'view_policy',     label: '📄 View my policy' },
          ],
        }
      );
      return;
    }

    // ── Payout options ───────────────────────────────────────────────────────
    if (buttonId === 'payment_options') { await startPayoutOptions(); return; }
    if (buttonId === 'payout_bank' || buttonId === 'payout_wallet') { await handlePayoutMethod(buttonId); return; }
    if (buttonId.startsWith('payout_wallet_')) { await handlePayoutWalletProvider(buttonId); return; }
    if (buttonId.startsWith('select_bank_')) {
      const bankName = buttonId.replace('select_bank_', '').replace(/_/g, ' ');
      updateUserData({ payoutBankName: bankName });
      setStep(STEPS.PAYOUT_SAVED);
      await botSay(
        `✅ *Payout details saved!*\n\n🏦 Bank: *${bankName}*\n💰 Account: *••••••••${stateRef.current.userData.payoutBankAccount?.slice(-2)}*\n\nWe'll use these details for any future payouts.`,
        'buttons',
        { buttons: [{ id: 'main_menu', label: '🏠 Main menu' }] }
      );
      return;
    }

    // ── Policy ───────────────────────────────────────────────────────────────
    if (buttonId === 'check_policy' || buttonId === 'view_policies') { await showPolicies(); return; }
    if (buttonId === 'view_policy') { await showPolicyDetail(0); return; }
    if (buttonId === 'lookup_phone_confirm') { await handlePolicyLookupValue('whatsapp_number'); return; }
    if (buttonId.startsWith('download_policy_')) {
      await botSay('📥 *Your policy document is ready*\n\n📄 Policy document has been sent to your WhatsApp.\n\nIf you don\'t receive it within a few minutes, please contact support.', 'text');
      return;
    }
    if (buttonId.startsWith('lookup_')) { await handlePolicyLookupMethod(buttonId); return; }
    if (buttonId.startsWith('cancel_policy_')) {
      const idx = parseInt(buttonId.replace('cancel_policy_', ''));
      await handleCancelPolicy(idx); return;
    }
    if (buttonId.startsWith('confirm_cancel_')) {
      const idx = parseInt(buttonId.replace('confirm_cancel_', ''));
      await confirmCancelPolicy(idx); return;
    }
    if (buttonId.startsWith('policy_')) {
      const idx = parseInt(buttonId.replace('policy_', ''));
      await showPolicyDetail(idx); return;
    }

    // ── Boarding pass ─────────────────────────────────────────────────────────
    if (buttonId === 'upload_boarding') { await startBoardingUpload(); return; }
    if (buttonId === 'boarding_upload_start') { await startBoardingUploadPrompt(); return; }
    if (buttonId === 'trigger_file_upload') {
      setInputMode('file', '');
      setStep(STEPS.BOARDING_UPLOAD);
      return;
    }

    // ── Link flight ───────────────────────────────────────────────────────────
    if (buttonId === 'link_flight') { await startLinkFlight(); return; }
    if (buttonId.startsWith('link_flight_policy_')) { await handleLinkPolicySelect(buttonId); return; }
    if (buttonId === 'confirm_link_flight') { await confirmLinkFlight(); return; }
    if (buttonId === 'retry_link_flight') {
      setStep(STEPS.LINK_ENTER_FLIGHT);
      await botSay('✈️ Please re-enter the flight number:', 'text');
      setInputMode('text', 'e.g. P47123');
      return;
    }

    // ── Update details ────────────────────────────────────────────────────────
    if (buttonId === 'update_details') { await startUpdateDetails(); return; }
    if (['update_name', 'update_email', 'update_phone', 'update_bank'].includes(buttonId)) {
      await handleUpdateField(buttonId); return;
    }

    // ── Help / FAQ ────────────────────────────────────────────────────────────
    if (buttonId === 'back_to_help') { await showHelp(); return; }
    if (buttonId === 'faq_contact_agent') {
      userSay('📞 Speak to an agent');
      await botSay(
        `📞 *Contact Support*\n\n🌐 www.travelassist.ng\n📧 support@travelassist.ng\n📱 WhatsApp: +234 800 TRAVEL\n🕐 Available 24/7`,
        'buttons',
        { buttons: [{ id: 'back_to_help', label: '⬅️ Back to help' }, { id: 'main_menu', label: '🏠 Main menu' }] }
      );
      return;
    }
    if (buttonId.startsWith('faq_')) { await showFAQAnswer(buttonId); return; }

    // ── Claims / Alerts ───────────────────────────────────────────────────────
    if (buttonId === 'trigger_flight_delay') {
      await triggerProactiveAlert('flight_delay');
      return;
    }
    if (buttonId === 'claim_payout') {
      userSay('💰 Claim payout');
      await botSay('⏳ Processing your claim...', 'status', { statusType: 'loading' });
      await new Promise((r) => setTimeout(r, 2000));
      await triggerProactiveAlert('payout_completed');
      return;
    }

    // ── Contact support ───────────────────────────────────────────────────────
    if (buttonId === 'contact_support') {
      userSay('📞 Contact support');
      await botSay(
        `📞 *Contact Support*\n\n🌐 www.travelassist.ng\n📧 support@travelassist.ng\n📱 WhatsApp: +234 800 TRAVEL\n🕐 Available 24/7`,
        'buttons',
        { buttons: [{ id: 'main_menu', label: '🏠 Main menu' }] }
      );
      return;
    }

    // ── Session expired (6.14) ────────────────────────────────────────────────
    if (buttonId === 'trigger_session_expired') {
      await botSay(
        `⏳ Your previous session expired\n\nWould you like to continue where you stopped?`,
        'buttons',
        {
          buttons: [
            { id: 'resume_session',  label: '✅ Yes' },
            { id: 'main_menu',       label: '🏠 Start again' },
          ],
        }
      );
      return;
    }
    if (buttonId === 'resume_session') {
      userSay('✅ Yes');
      await showMainMenu(false);
      return;
    }

    // ── System unavailable (6.15) ─────────────────────────────────────────────
    if (buttonId === 'trigger_system_unavailable') {
      await botSay(
        `⚠️ We're unable to complete that right now\n\nPlease try again shortly`,
        'buttons',
        {
          buttons: [
            { id: 'system_retry', label: '🔄 Try again' },
            { id: 'help',         label: '🙋 Help' },
          ],
        }
      );
      return;
    }
    if (buttonId === 'system_retry') {
      userSay('🔄 Try again');
      await showMainMenu(false);
      return;
    }
    } finally {
      processingRef.current = false;
    }
  }, [
    botSay,
    confirmCancelPolicy,
    confirmLinkFlight,
    generatePolicyNumber,
    handleArriveAirportSelect,
    handleCancelPolicy,
    handleCarrier,
    handleCoverType,
    handleDepartAirportSelect,
    handleKYCConsent,
    handleKYCTypeSelect,
    handleLinkPolicySelect,
    handlePaymentMethod,
    handlePayoutMethod,
    handlePayoutWalletProvider,
    handlePolicyLookupMethod,
    handleSelectPlan,
    handleTripType,
    handleUpdateField,
    handleWalletProviderSelected,
    processKYC,
    processPayment,
    setInputMode,
    setStep,
    showComparePlans,
    showFAQAnswer,
    showHelp,
    showMainMenu,
    showPolicies,
    showPolicyDetail,
    startBoardingUpload,
    startBoardingUploadPrompt,
    startBuyCover,
    startKYC,
    startLinkFlight,
    startPaymentFlow,
    startPayoutOptions,
    startUpdateDetails,
    triggerProactiveAlert,
    updateUserData,
    userSay,
  ]);

  const handleTextInput = useCallback(async (text) => {
    // Quick commands always bypass the processing lock
    if (text === '99') { processingRef.current = false; await handleButtonClick('99'); return; }
    if (text === '00') { processingRef.current = false; await handleButtonClick('main_menu'); return; }
    if (text === '0')  { processingRef.current = false; await handleButtonClick('back'); return; }
    if (text === '9')  { processingRef.current = false; await handleButtonClick('main_menu'); return; }

    if (processingRef.current) return;
    processingRef.current = true;
    const step = stateRef.current.currentStep;
    try {

    // Buy cover flow text inputs
    if (step === STEPS.BUY_TRAVELLER_COUNT)  { await handleTravellerCount(text.trim()); return; }
    if (step === STEPS.BUY_TRAVELLER_NAMES || step === STEPS.BUY_MORE_TRAVELLERS)  { await handleTravellerName(text.trim()); return; }
    if (step === STEPS.BUY_EMAIL)            { await handleEmailEntered(text.trim()); return; }
    if (step === STEPS.BUY_BOOKING_REF)      { await handleBookingRef(text.trim()); return; }
    if (step === STEPS.BUY_ENTER_FLIGHT || step === STEPS.FLIGHT_NOT_FOUND) {
      await handleFlightNumberInput(text.trim()); return;
    }
    if (step === STEPS.BUY_TRAVEL_DATE)      { await handleTravelDate(text.trim()); return; }
    if (step === STEPS.BUY_TRAVEL_TIME)      { await handleTravelTime(text.trim()); return; }
    if (step === STEPS.BUY_DEPART_AIRPORT_QUERY || step === STEPS.BUY_DEPART_AIRPORT_SELECT) {
      await handleDepartAirportQuery(text.trim()); return;
    }
    if (step === STEPS.BUY_ARRIVE_TIME)      { await handleArriveTime(text.trim()); return; }
    if (step === STEPS.BUY_ARRIVE_AIRPORT_QUERY || step === STEPS.BUY_ARRIVE_AIRPORT_SELECT) {
      await handleArriveAirportQuery(text.trim()); return;
    }
    if (step === STEPS.BUY_CARRIER)          { await handleCarrier(text.trim()); return; }

    // KYC
    if (step === STEPS.KYC_ENTER_BVN || step === STEPS.KYC_ENTER_NIN) {
      await handleKYCValueEntered(text.trim()); return;
    }

    // Wallet phone (payment)
    if (step === STEPS.PAYMENT_WALLET_PHONE) { await handleWalletPhoneEntered(text.trim()); return; }

    // Payout flow text inputs
    if (step === STEPS.PAYOUT_BANK_ACCOUNT)  { await handlePayoutBankAccount(text.trim()); return; }
    if (step === STEPS.PAYOUT_BANK_NAME)     { await handlePayoutBankName(text.trim()); return; }
    if (step === STEPS.PAYOUT_WALLET_PHONE)  { await handlePayoutWalletPhone(text.trim()); return; }

    // Policy lookup
    if (step === STEPS.POLICY_LOOKUP)        { await handlePolicyLookupValue(text.trim()); return; }

    // Link flight text input
    if (step === STEPS.LINK_ENTER_FLIGHT)    { await handleLinkFlightEntered(text.trim()); return; }

    // Update details text inputs
    if ([STEPS.UPDATE_NAME, STEPS.UPDATE_EMAIL, STEPS.UPDATE_PHONE, STEPS.UPDATE_BANK].includes(step)) {
      await handleUpdateValue(text.trim()); return;
    }

    // Fallback
    userSay(text);
    await botSay(
      `⚠️ Sorry, I didn't understand that\n\nPlease reply with one of the menu numbers\nor type 00 for main menu`,
      'buttons',
      { buttons: [{ id: 'main_menu', label: '🏠 Main menu' }, { id: 'help', label: '🙋 Help' }] }
    );
    } finally {
      processingRef.current = false;
    }
  }, [
    botSay,
    handleArriveAirportQuery,
    handleArriveTime,
    handleBookingRef,
    handleButtonClick,
    handleCarrier,
    handleDepartAirportQuery,
    handleEmailEntered,
    handleFlightLookup,
    handleFlightNumberInput,
    handleKYCValueEntered,
    handleLinkFlightEntered,
    handlePayoutBankAccount,
    handlePayoutBankName,
    handlePayoutWalletPhone,
    handlePolicyLookupValue,
    handleTravelDate,
    handleTravelTime,
    handleTravellerCount,
    handleTravellerName,
    handleUpdateValue,
    handleWalletPhoneEntered,
    userSay,
  ]);

  const value = {
    state,
    startSession,
    handleButtonClick,
    handleTextInput,
    handleBoardingPassUploaded,
    triggerProactiveAlert,
    showMainMenu,
  };

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error('useChat must be used within ChatProvider');
  return ctx;
}
