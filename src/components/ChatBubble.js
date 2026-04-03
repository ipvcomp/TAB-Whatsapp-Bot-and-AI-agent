import React from 'react';

function formatTime(date) {
  return new Date(date).toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

function TickIcon({ color = '#34B7F1' }) {
  return (
    <svg width="16" height="11" viewBox="0 0 16 11" fill="none" className="inline-block ml-1">
      <path d="M1 5.5L5 9.5L15 1" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M5 9.5L9 5.5" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

export default function ChatBubble({ message }) {
  const isBot = message.sender === 'bot';

  if (message.type === 'status') {
    return <StatusBubble message={message} />;
  }

  return (
    <div
      className={`flex w-full mb-1 animate-fade-in ${isBot ? 'justify-start' : 'justify-end'}`}
    >
      {isBot && (
        <div className="w-8 h-8 rounded-full bg-whatsapp-header flex items-center justify-center flex-shrink-0 mr-1 mt-auto mb-1">
          <span className="text-white text-xs font-bold">TA</span>
        </div>
      )}
      <div
        className={`relative max-w-[85%] ${
          isBot ? 'bg-white' : 'bg-whatsapp-user'
        } rounded-2xl px-3 py-2 shadow-sm ${
          isBot ? 'rounded-tl-sm' : 'rounded-tr-sm'
        }`}
        style={{ minWidth: '80px' }}
      >
        {/* Tail */}
        {isBot && (
          <div
            className="absolute top-0 -left-2 w-0 h-0 border-t-0"
            style={{
              borderRight: '8px solid white',
              borderTop: '8px solid transparent',
              borderBottom: '0 solid transparent',
            }}
          />
        )}
        {!isBot && (
          <div
            className="absolute top-0 -right-2 w-0 h-0"
            style={{
              borderLeft: '8px solid #DCF8C6',
              borderTop: '8px solid transparent',
              borderBottom: '0 solid transparent',
            }}
          />
        )}

        <MessageContent message={message} isBot={isBot} />

        <div className={`flex items-center gap-1 mt-1 ${isBot ? 'justify-start' : 'justify-end'}`}>
          <span className="text-xs text-gray-400">{formatTime(message.timestamp)}</span>
          {!isBot && <TickIcon />}
        </div>
      </div>
    </div>
  );
}

function MessageContent({ message, isBot }) {
  const text = message.content;
  if (!text) return null;

  // Simple markdown parsing: **bold**, *italic*, `code`
  const parsed = parseMarkdown(text);

  return (
    <div className="text-sm text-gray-800 whitespace-pre-wrap break-words leading-relaxed">
      {parsed}
    </div>
  );
}

function parseMarkdown(text) {
  if (!text) return null;

  // Split on markdown tokens
  const parts = [];
  const regex = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(<strong key={match.index} className="font-semibold">{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*') && token.endsWith('*')) {
      parts.push(<em key={match.index}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={match.index} className="bg-gray-100 text-gray-700 px-1 rounded text-xs font-mono">{token.slice(1, -1)}</code>
      );
    }
    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : text;
}

function StatusBubble({ message }) {
  const { statusType } = message;

  const bgMap = {
    loading: 'bg-blue-50 border border-blue-100',
    success: 'bg-green-50 border border-green-100',
    error: 'bg-red-50 border border-red-100',
  };

  const iconMap = {
    loading: (
      <div className="flex gap-1 items-center">
        {[0, 150, 300].map((d) => (
          <div
            key={d}
            className="w-2 h-2 rounded-full bg-blue-400 animate-bounce-dot"
            style={{ animationDelay: `${d}ms` }}
          />
        ))}
      </div>
    ),
    success: <span className="text-green-500 text-lg">✅</span>,
    error: <span className="text-red-500 text-lg">❌</span>,
  };

  return (
    <div className="flex justify-start w-full mb-1 animate-fade-in">
      <div className={`flex items-center gap-2 px-3 py-2 rounded-xl text-sm max-w-[85%] shadow-sm ${bgMap[statusType] || 'bg-white'}`}>
        {iconMap[statusType]}
        <span className="text-gray-700">{parseMarkdown(message.content)}</span>
        <span className="text-xs text-gray-400 ml-auto pl-2">{formatTime(message.timestamp)}</span>
      </div>
    </div>
  );
}
