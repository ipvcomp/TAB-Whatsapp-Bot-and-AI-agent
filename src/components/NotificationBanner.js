import React, { useState } from 'react';
import { useChat } from '../context/ChatContext';

export default function NotificationBanner() {
  const { triggerProactiveAlert } = useChat();
  const [dismissed, setDismissed] = useState(false);
  const [, setTriggered] = useState(false);

  if (dismissed) return null;

  const handleTrigger = async (type) => {
    setTriggered(true);
    setDismissed(true);
    await triggerProactiveAlert(type);
  };

  return (
    <div className="relative mx-3 mt-2 bg-amber-50 border border-amber-200 rounded-xl p-3 shadow-sm animate-fade-in">
      <button
        onClick={() => setDismissed(true)}
        className="absolute top-2 right-2 text-gray-400 hover:text-gray-600 text-lg leading-none"
      >
        ×
      </button>
      <div className="flex items-start gap-2 pr-4">
        <span className="text-xl flex-shrink-0">⚡</span>
        <div>
          <p className="text-xs font-semibold text-amber-800">Proactive Alert Simulator</p>
          <p className="text-xs text-amber-700 mt-0.5">Trigger demo alerts to test the experience:</p>
          <div className="flex flex-wrap gap-1.5 mt-2">
            <button
              onClick={() => handleTrigger('flight_delay')}
              className="text-xs bg-amber-200 text-amber-800 px-2 py-1 rounded-full hover:bg-amber-300 active:scale-95 transition-all"
            >
              ⚠️ Flight Delay
            </button>
            <button
              onClick={() => handleTrigger('payout')}
              className="text-xs bg-green-200 text-green-800 px-2 py-1 rounded-full hover:bg-green-300 active:scale-95 transition-all"
            >
              💰 Payout Alert
            </button>
            <button
              onClick={() => handleTrigger('policy_issued')}
              className="text-xs bg-blue-200 text-blue-800 px-2 py-1 rounded-full hover:bg-blue-300 active:scale-95 transition-all"
            >
              🎫 Policy Alert
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
