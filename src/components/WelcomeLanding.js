import React from 'react';
import { useChat } from '../context/ChatContext';

const MOCK_BOARD = [
  { time: '10:30', destination: 'Abuja (ABV)',      num: 'P47123', status: 'DELAYED',    color: 'text-yellow-400' },
  { time: '11:00', destination: 'Port Harcourt (PHC)', num: 'QI402', status: 'ON TIME',  color: 'text-green-400' },
  { time: '14:00', destination: 'Kano (KAN)',       num: 'W3501',  status: 'CANCELLED', color: 'text-red-400' },
  { time: '16:45', destination: 'London (LHR)',     num: 'AA123',  status: 'DELAYED',   color: 'text-yellow-400' },
];

export default function WelcomeLanding({ onStart }) {
  const { handleButtonClick } = useChat();

  const handleAction = (id) => {
    onStart();           // collapses landing, starts chat
    handleButtonClick(id);
  };

  return (
    <div className="flex flex-col h-full overflow-y-auto scrollbar-hide">

      {/* ── Hero Banner ── */}
      <div
        className="relative overflow-hidden flex-shrink-0"
        style={{
          background: 'linear-gradient(135deg, #e8f4fd 0%, #c8e6f9 50%, #d4edda 100%)',
          minHeight: '200px',
        }}
      >
        {/* Decorative circles */}
        <div className="absolute -top-6 -right-6 w-24 h-24 rounded-full bg-blue-200 opacity-40" />
        <div className="absolute top-12 -right-3 w-14 h-14 rounded-full bg-teal-200 opacity-50" />
        <div className="absolute -bottom-4 left-8 w-20 h-20 rounded-full bg-green-200 opacity-30" />

        {/* Brand row */}
        <div className="relative flex items-center gap-2 px-5 pt-5">
          <div className="w-9 h-9 rounded-xl bg-whatsapp-header flex items-center justify-center shadow-md">
            <span className="text-white text-lg">✈️</span>
          </div>
          <span className="font-bold text-gray-800 text-base tracking-tight">TravelAssist</span>
          <span className="ml-auto text-xs text-gray-500 bg-white bg-opacity-60 px-2 py-0.5 rounded-full">
            Powered by iPurvey
          </span>
        </div>

        {/* Headline */}
        <div className="relative px-5 pt-4 pb-2">
          <h1 className="text-xl font-extrabold text-gray-800 leading-snug">
            Get <span className="text-whatsapp-header">compensated</span> for<br />
            travel delays &amp; cancellations!
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Instant cover · Automatic payouts · 24/7 support
          </p>
        </div>

        {/* 3D emoji illustrations */}
        <div className="absolute bottom-3 right-4 flex items-end gap-1 pointer-events-none select-none">
          <span className="text-4xl" style={{ filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.15))' }}>✈️</span>
          <span className="text-2xl mb-1" style={{ filter: 'drop-shadow(0 2px 4px rgba(0,0,0,0.10))' }}>🎈</span>
          <span className="text-3xl" style={{ filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.15))' }}>🚄</span>
        </div>
      </div>

      {/* ── Action Buttons (iPurvey style) ── */}
      <div className="bg-white px-4 pt-4 pb-3 flex flex-col gap-2.5 flex-shrink-0 shadow-sm">
        {[
          { id: 'buy_cover',       emoji: '✈️', label: 'Buy cover'             },
          { id: 'check_policy',    emoji: '📄', label: 'Check my policy'       },
          { id: 'update_details',  emoji: '✏️', label: 'Update my details'     },
          { id: 'upload_boarding', emoji: '🛂', label: 'Upload boarding pass'  },
          { id: 'help',            emoji: '🙋', label: 'Help'                  },
        ].map((btn) => (
          <button
            key={btn.id}
            onClick={() => handleAction(btn.id)}
            className="
              w-full flex items-center gap-3 px-4 py-3
              bg-white border border-gray-200 rounded-2xl
              shadow-sm hover:shadow-md hover:border-teal-400
              active:scale-[0.98] transition-all duration-150
              text-left
            "
          >
            <span className="w-8 h-8 rounded-full bg-teal-50 flex items-center justify-center flex-shrink-0 text-base">
              {btn.emoji}
            </span>
            <span className="font-semibold text-gray-700 text-sm">{btn.label}</span>
            <svg className="w-4 h-4 text-gray-300 ml-auto flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        ))}
      </div>

      {/* ── Live Flight Board ── */}
      <div className="mx-4 mt-3 mb-3 flex-shrink-0">
        <div className="bg-gray-900 rounded-2xl overflow-hidden shadow-lg">
          {/* Board header */}
          <div className="bg-gray-800 px-4 py-2 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <span className="text-gray-300 text-xs font-semibold tracking-wider uppercase">Live Flight Board</span>
            <span className="ml-auto text-gray-500 text-xs">Lagos (LOS)</span>
          </div>

          {/* Column header */}
          <div className="grid grid-cols-4 px-4 py-1.5 border-b border-gray-700">
            {['Time', 'Destination', 'Flt', 'Status'].map((h) => (
              <span key={h} className="text-gray-500 text-xs font-medium">{h}</span>
            ))}
          </div>

          {/* Rows */}
          {MOCK_BOARD.map((row, i) => (
            <div
              key={i}
              className={`grid grid-cols-4 px-4 py-2 ${i < MOCK_BOARD.length - 1 ? 'border-b border-gray-800' : ''}`}
            >
              <span className="text-gray-300 text-xs">{row.time}</span>
              <span className="text-gray-200 text-xs truncate pr-1">{row.destination}</span>
              <span className="text-gray-400 text-xs">{row.num}</span>
              <span className={`text-xs font-bold ${row.color}`}>{row.status}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Footer ── */}
      <div className="text-center pb-4 flex-shrink-0">
        <p className="text-xs text-gray-400">www.travelassist.ng</p>
        <p className="text-xs text-gray-300 mt-0.5">🔒 Regulated · Secure · Instant payouts</p>
      </div>
    </div>
  );
}
