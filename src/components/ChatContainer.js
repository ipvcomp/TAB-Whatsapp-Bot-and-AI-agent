import React, { useEffect, useRef, useState } from 'react';
import { useChat } from '../context/ChatContext';
import MessageRow from './MessageRow';
import TypingIndicator from './TypingIndicator';
import InputArea from './InputArea';
import NotificationBanner from './NotificationBanner';
import WelcomeLanding from './WelcomeLanding';

export default function ChatContainer() {
  const { state } = useChat();
  const { messages, isTyping } = state;
  const bottomRef = useRef(null);
  const sessionStarted = useRef(false);
  const [showLanding, setShowLanding] = useState(true);

  // Don't auto-start session — landing handles the first action
  useEffect(() => {
    // nothing — session starts when user taps a landing button
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  // Once messages start appearing, keep landing hidden
  useEffect(() => {
    if (messages.length > 0) setShowLanding(false);
  }, [messages]);

  const handleLandingStart = () => {
    setShowLanding(false);
    if (!sessionStarted.current) {
      sessionStarted.current = true;
      // startSession is skipped — landing button calls handleButtonClick directly
    }
  };

  if (showLanding) {
    return <WelcomeLanding onStart={handleLandingStart} />;
  }

  return (
    <div className="flex flex-col h-full chat-bg">
      {/* Scrollable chat area */}
      <div className="flex-1 overflow-y-auto scrollbar-hide px-2 pt-3 pb-2">
        <NotificationBanner />
        <div className="mt-2">
          {messages.map((msg) => (
            <MessageRow key={msg.id} message={msg} />
          ))}
          {isTyping && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input area — always at bottom */}
      <InputArea />
    </div>
  );
}
