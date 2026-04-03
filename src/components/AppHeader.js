import React, { useState } from 'react';
import { useChat } from '../context/ChatContext';

export default function AppHeader() {
  const { handleButtonClick } = useChat();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div
      className="flex items-center gap-3 px-4 py-3 shadow-md z-10 relative"
      style={{ backgroundColor: '#075E54' }}
    >
      {/* Avatar */}
      <div className="w-10 h-10 rounded-full bg-gradient-to-br from-green-400 to-teal-500 flex items-center justify-center flex-shrink-0 shadow-sm">
        <span className="text-white text-lg">✈️</span>
      </div>

      {/* Title */}
      <div className="flex-1 min-w-0">
        <h1 className="text-white font-semibold text-base leading-tight">TravelAssist</h1>
        <p className="text-green-300 text-xs">Travel Insurance Bot • Online</p>
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-3 relative">
        {/* Help button */}
        <button
          onClick={() => handleButtonClick('help')}
          title="Help"
          className="text-white opacity-90 hover:opacity-100 transition-opacity"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </button>

        {/* Kebab menu */}
        <button
          onClick={() => setMenuOpen((o) => !o)}
          title="Menu"
          className="text-white opacity-90 hover:opacity-100 transition-opacity"
        >
          <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
            <circle cx="12" cy="5" r="1.5" />
            <circle cx="12" cy="12" r="1.5" />
            <circle cx="12" cy="19" r="1.5" />
          </svg>
        </button>

        {menuOpen && (
          <div className="absolute top-8 right-0 bg-white rounded-xl shadow-lg py-2 z-20 min-w-[160px] animate-fade-in">
            {[
              { id: 'main_menu', label: '🏠 Main Menu', cmd: 'main_menu' },
              { id: 'check_policy', label: '📋 My Policies', cmd: 'check_policy' },
              { id: 'track_flight', label: '✈️ Track Flight', cmd: 'track_flight' },
              { id: 'help', label: '❓ Help', cmd: 'help' },
            ].map((item) => (
              <button
                key={item.id}
                onClick={() => { setMenuOpen(false); handleButtonClick(item.cmd); }}
                className="w-full text-left px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 active:bg-gray-100 transition-colors"
              >
                {item.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {menuOpen && (
        <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
      )}
    </div>
  );
}
