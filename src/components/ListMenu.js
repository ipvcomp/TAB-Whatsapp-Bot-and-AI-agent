import React from 'react';
import { useChat } from '../context/ChatContext';

export default function ListMenu({ items, footer }) {
  const { handleButtonClick } = useChat();

  return (
    <div className="mt-2 mb-1 animate-fade-in">
      <div className="bg-white rounded-xl shadow-sm overflow-hidden border border-gray-100">
        {items.map((item, idx) => (
          <button
            key={item.id}
            onClick={() => handleButtonClick(item.id)}
            className={`
              w-full text-left px-4 py-3 text-sm
              hover:bg-gray-50 active:bg-gray-100
              transition-colors duration-100
              flex items-center justify-between
              ${idx < items.length - 1 ? 'border-b border-gray-100' : ''}
            `}
          >
            <div>
              <div className="font-medium text-gray-800">{item.label}</div>
              {item.subtitle && (
                <div className="text-xs text-gray-400 mt-0.5">{item.subtitle}</div>
              )}
            </div>
            <svg
              className="w-4 h-4 text-gray-400 flex-shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        ))}
      </div>

      {footer && footer.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-2 justify-end pr-1">
          {footer.map((btn) => (
            <button
              key={btn.id}
              onClick={() => handleButtonClick(btn.id)}
              className="
                bg-gray-100 text-gray-600 border border-gray-200
                text-xs font-medium px-3 py-1.5 rounded-full
                hover:bg-gray-200 active:scale-95 transition-all
                shadow-sm whitespace-nowrap
              "
            >
              {btn.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
