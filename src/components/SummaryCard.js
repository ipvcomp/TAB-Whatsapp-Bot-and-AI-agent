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

const SUMMARY_STYLES = {
  order: { border: 'border-green-400', bg: 'bg-green-50', icon: '📋', label: 'Order Summary' },
  kyc: { border: 'border-blue-400', bg: 'bg-blue-50', icon: '🔐', label: 'KYC Summary' },
  payment: { border: 'border-yellow-400', bg: 'bg-yellow-50', icon: '💳', label: 'Payment Summary' },
};

export default function SummaryCard({ message }) {
  const { summaryType, buttons } = message;
  const style = SUMMARY_STYLES[summaryType] || SUMMARY_STYLES.order;

  return (
    <div className="flex justify-start w-full mb-1 animate-fade-in">
      <div className="max-w-[90%] w-full">
        <div className={`${style.bg} border-l-4 ${style.border} bg-white rounded-xl shadow-sm p-4`}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-lg">{style.icon}</span>
            <span className="font-semibold text-sm text-gray-700">{style.label}</span>
          </div>
          <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
            {parseMarkdown(message.content)}
          </div>
          <div className="text-xs text-gray-400 mt-2">
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
