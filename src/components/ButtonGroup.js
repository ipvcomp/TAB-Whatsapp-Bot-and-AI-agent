import React from 'react';
import { useChat } from '../context/ChatContext';

export default function ButtonGroup({ buttons }) {
  const { handleButtonClick } = useChat();

  if (!buttons || buttons.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-2 mb-1 justify-end pr-2 animate-fade-in">
      {buttons.map((btn) => (
        <button
          key={btn.id}
          onClick={() => handleButtonClick(btn.id)}
          className="
            bg-white text-whatsapp-header border border-whatsapp-header
            text-sm font-medium px-3 py-2 rounded-full
            hover:bg-whatsapp-header hover:text-white
            active:scale-95 transition-all duration-150
            shadow-sm whitespace-nowrap
          "
        >
          {btn.label}
        </button>
      ))}
    </div>
  );
}
