import React from 'react';
import { ChatProvider } from './context/ChatContext';
import AppHeader from './components/AppHeader';
import ChatContainer from './components/ChatContainer';
import './App.css';

function App() {
  return (
    <ChatProvider>
      {/* Phone shell — centers content on desktop */}
      <div className="min-h-screen bg-gray-600 flex items-center justify-center p-4">
        <div
          className="relative flex flex-col bg-white shadow-2xl overflow-hidden"
          style={{
            width: '100%',
            maxWidth: '420px',
            height: '100vh',
            maxHeight: '860px',
            borderRadius: '16px',
          }}
        >
          <AppHeader />
          <div className="flex-1 overflow-hidden">
            <ChatContainer />
          </div>
        </div>
      </div>
    </ChatProvider>
  );
}

export default App;
