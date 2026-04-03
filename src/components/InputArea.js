import React, { useState, useRef } from 'react';
import { useChat } from '../context/ChatContext';

export default function InputArea() {
  const { state, handleTextInput, handleBoardingPassUploaded } = useChat();
  const { inputMode, inputPlaceholder, isTyping } = state;
  const [value, setValue] = useState('');
  const fileRef = useRef(null);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!value.trim()) return;
    handleTextInput(value.trim());
    setValue('');
  };

  const handleFileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    handleBoardingPassUploaded(file.name);
    e.target.value = '';
  };

  if (inputMode === 'file') {
    return (
      <div className="px-3 py-3 bg-whatsapp-input border-t border-gray-200 animate-slide-up">
        <input
          type="file"
          ref={fileRef}
          onChange={handleFileChange}
          accept=".pdf,.jpg,.jpeg,.png,.gif,.tiff,.tif"
          className="hidden"
        />
        <button
          onClick={() => fileRef.current?.click()}
          className="
            w-full flex items-center justify-center gap-2
            bg-whatsapp-header text-white py-3 rounded-xl
            font-medium text-sm shadow-sm
            active:scale-98 transition-all
          "
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
          </svg>
          📎 Tap to Upload Boarding Pass
        </button>
        <p className="text-xs text-gray-400 text-center mt-1.5">JPEG · PDF · GIF · TIFF · PNG — Max 20MB</p>
      </div>
    );
  }

  if (inputMode === 'text') {
    return (
      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 px-3 py-2 bg-whatsapp-input border-t border-gray-200 animate-slide-up"
      >
        <div className="flex-1 bg-white rounded-full px-4 py-2.5 shadow-sm border border-gray-200 flex items-center">
          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={inputPlaceholder || 'Type a message...'}
            className="flex-1 bg-transparent outline-none text-sm text-gray-700 placeholder-gray-400"
            autoFocus
          />
        </div>
        <button
          type="submit"
          disabled={!value.trim() || isTyping}
          className={`
            w-10 h-10 rounded-full flex items-center justify-center
            shadow-sm transition-all duration-150
            ${value.trim() && !isTyping
              ? 'bg-whatsapp-header text-white active:scale-95'
              : 'bg-gray-200 text-gray-400 cursor-not-allowed'
            }
          `}
        >
          <svg className="w-5 h-5 rotate-90" fill="currentColor" viewBox="0 0 24 24">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
          </svg>
        </button>
      </form>
    );
  }

  // No input (button-only mode) — show a dimmed hint bar
  return (
    <div className="flex items-center gap-2 px-4 py-2.5 bg-whatsapp-input border-t border-gray-200">
      <div className="flex-1 bg-white rounded-full px-4 py-2.5 border border-gray-200 opacity-50 cursor-not-allowed">
        <span className="text-sm text-gray-400">Reply using the buttons above</span>
      </div>
    </div>
  );
}
