import React from 'react';
import ChatBubble from './ChatBubble';
import ButtonGroup from './ButtonGroup';
import ListMenu from './ListMenu';
import SummaryCard from './SummaryCard';
import MediaCard from './MediaCard';

/**
 * Renders a single message row, including the bubble and any interactive elements.
 */
export default function MessageRow({ message }) {
  const isBot = message.sender === 'bot';

  // Non-interactive (user bubbles or bot text-only)
  if (!isBot || (message.type === 'text' && !message.buttons)) {
    return <ChatBubble message={message} />;
  }

  if (message.type === 'status') {
    return <ChatBubble message={message} />;
  }

  if (message.type === 'card') {
    return <MediaCard message={message} />;
  }

  if (message.type === 'summary') {
    return <SummaryCard message={message} />;
  }

  if (message.type === 'buttons') {
    return (
      <div className="w-full">
        <ChatBubble message={{ ...message, type: 'text' }} />
        {message.buttons && <ButtonGroup buttons={message.buttons} />}
      </div>
    );
  }

  if (message.type === 'list') {
    return (
      <div className="w-full">
        <ChatBubble message={{ ...message, type: 'text' }} />
        <ListMenu items={message.items || []} footer={message.footer} listType={message.listType} />
      </div>
    );
  }

  return <ChatBubble message={message} />;
}
