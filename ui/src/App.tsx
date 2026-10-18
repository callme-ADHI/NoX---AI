import React, { useEffect, useState } from 'react';
import { NavRail, TabType } from './components/NavRail';
import { CommandPalette } from './components/CommandPalette';
import { ChatPage } from './pages/ChatPage';
import { BriefingsPage } from './pages/BriefingsPage';
import { JobsPage } from './pages/JobsPage';
import { MemoryPage } from './pages/MemoryPage';
import { ActivityPage } from './pages/ActivityPage';
import { SettingsPage } from './pages/SettingsPage';
import { SystemStatus } from './types';
import { api } from './services/api';
import { wsService } from './services/websocket';
import { WifiOff, RefreshCw } from 'lucide-react';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabType>('chat');
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState<boolean>(false);

  useEffect(() => {
    // 1. Fetch system status
    api.getStatus()
      .then(setStatus)
      .catch((err) => console.warn('Could not reach daemon status:', err));

    // 2. Connect WebSocket
    wsService.connect();
    const unsubStatus = wsService.onStatusChange(setWsConnected);

    return () => {
      unsubStatus();
      wsService.disconnect();
    };
  }, []);

  const renderActivePage = () => {
    switch (activeTab) {
      case 'chat':
        return <ChatPage />;
      case 'briefings':
        return <BriefingsPage />;
      case 'jobs':
        return <JobsPage />;
      case 'memory':
        return <MemoryPage />;
      case 'activity':
        return <ActivityPage />;
      case 'settings':
        return <SettingsPage />;
      default:
        return <ChatPage />;
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-text font-sans antialiased">
      {/* Slim Left Navigation Rail (Desktop) */}
      <NavRail
        activeTab={activeTab}
        onTabChange={setActiveTab}
        status={status}
        wsConnected={wsConnected}
      />

      {/* Main App Container */}
      <div className="flex-1 flex flex-col overflow-hidden relative">
        {/* Reconnecting banner if WebSocket drops */}
        {!wsConnected && (
          <div className="bg-danger/20 border-b border-danger/40 px-4 py-1.5 flex items-center justify-between text-xs text-danger select-none z-30">
            <div className="flex items-center gap-2">
              <WifiOff className="w-3.5 h-3.5 animate-pulse" />
              <span>Connecting to NoX Daemon... Events will sync upon reconnection.</span>
            </div>
            <button
              onClick={() => wsService.connect()}
              className="flex items-center gap-1 font-semibold hover:underline"
            >
              <RefreshCw className="w-3 h-3" />
              Retry Now
            </button>
          </div>
        )}

        {/* Active Page View */}
        <div className="flex-1 flex overflow-hidden">{renderActivePage()}</div>
      </div>

      {/* Global Command Palette (Ctrl+K) */}
      <CommandPalette
        isOpen={isCommandPaletteOpen}
        onClose={() => setIsCommandPaletteOpen(false)}
        onSelectTab={setActiveTab}
        onNewChat={() => {
          // Handled via state event or tab switch
        }}
      />
    </div>
  );
};

export default App;
