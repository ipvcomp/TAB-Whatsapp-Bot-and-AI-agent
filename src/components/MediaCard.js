import React from 'react';
import ButtonGroup from './ButtonGroup';

function parseMarkdown(text) {
  if (!text) return null;
  const parts = [];
  const regex = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let lastIndex = 0;
  let match;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    const token = match[0];
    if (token.startsWith('**')) parts.push(<strong key={match.index} className="font-semibold">{token.slice(2, -2)}</strong>);
    else if (token.startsWith('*')) parts.push(<em key={match.index}>{token.slice(1, -1)}</em>);
    else if (token.startsWith('`')) parts.push(<code key={match.index} className="bg-gray-100 text-gray-700 px-1 rounded text-xs font-mono">{token.slice(1, -1)}</code>);
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts.length ? parts : text;
}

const CARD_STYLES = {
  flight: {
    header: 'bg-gradient-to-br from-blue-500 to-blue-700',
    icon: '✈️',
    title: 'Flight Details',
  },
  plan: {
    header: 'bg-gradient-to-br from-green-500 to-green-700',
    icon: '🛡️',
    title: 'Cover Plan',
  },
  policy_issued: {
    header: 'bg-gradient-to-br from-emerald-500 to-teal-600',
    icon: '🎫',
    title: 'Policy Issued',
  },
  policy_detail: {
    header: 'bg-gradient-to-br from-indigo-500 to-purple-600',
    icon: '📄',
    title: 'Policy Details',
  },
  kyc_success: {
    header: 'bg-gradient-to-br from-green-400 to-emerald-600',
    icon: '✅',
    title: 'Identity Verified',
  },
  boarding_success: {
    header: 'bg-gradient-to-br from-teal-500 to-cyan-600',
    icon: '🎫',
    title: 'Boarding Pass Verified',
  },
  flight_status: {
    header: 'bg-gradient-to-br from-sky-500 to-blue-600',
    icon: '📡',
    title: 'Flight Status',
  },
  payout_success: {
    header: 'bg-gradient-to-br from-yellow-400 to-orange-500',
    icon: '💰',
    title: 'Payout Processed',
  },
};

export default function MediaCard({ message }) {
  const { cardType, buttons } = message;
  const style = CARD_STYLES[cardType] || CARD_STYLES.flight;

  return (
    <div className="flex justify-start w-full mb-1 animate-fade-in">
      <div className="max-w-[90%] w-full">
        <div className="bg-white rounded-2xl shadow-md overflow-hidden border border-gray-100">
          {/* Header */}
          <div className={`${style.header} px-4 py-3 flex items-center gap-2`}>
            <span className="text-2xl">{style.icon}</span>
            <span className="text-white font-semibold text-sm">{style.title}</span>
          </div>

          {/* Content */}
          <div className="px-4 py-3 text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
            {parseMarkdown(message.content)}
          </div>

          {/* Timestamp */}
          <div className="px-4 pb-2 text-xs text-gray-400">
            {new Date(message.timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
          </div>
        </div>

        {buttons && buttons.length > 0 && (
          <ButtonGroup buttons={buttons} />
        )}
      </div>
    </div>
  );
}
