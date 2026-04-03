import React from 'react';

export default function TypingIndicator() {
  return (
    <div className="flex items-end gap-1 mb-2 animate-fade-in">
      <div className="w-8 h-8 rounded-full bg-whatsapp-header flex items-center justify-center flex-shrink-0">
        <span className="text-white text-xs font-bold">TA</span>
      </div>
      <div className="bg-white rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm flex items-center gap-1.5">
        {[0, 150, 300].map((delay) => (
          <div
            key={delay}
            className="w-2 h-2 rounded-full bg-gray-400"
            style={{
              animation: 'bounceDot 1.2s infinite',
              animationDelay: `${delay}ms`,
            }}
          />
        ))}
      </div>
    </div>
  );
}
