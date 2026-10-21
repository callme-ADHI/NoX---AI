import React, { useEffect, useState } from 'react';
import { NavRail, TabType } from './components/NavRail';
import { CommandPalette } from './components/CommandPalette';
import { ChatPage } from './pages/ChatPage';
import { BriefingsPage } from './pages/BriefingsPage';
import { JobsPage } from './pages/JobsPage';
import { TasksPage } from './pages/TasksPage';
import { MemoryPage } from './pages/MemoryPage';
import { ActivityPage } from './pages/ActivityPage';
import { SettingsPage } from './pages/SettingsPage';
import { SystemStatus, WebSocketEvent } from './types';
import { api } from './services/api';
import { wsService } from './services/websocket';
import { WifiOff, RefreshCw, Bell, X } from 'lucide-react';

interface InAppNotification {
  id: string;
  title: string;
  message: string;
  kind?: string;
  timestamp: number;
}

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabType>('chat');
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState<boolean>(false);
  const [notifications, setNotifications] = useState<InAppNotification[]>([]);

  useEffect(() => {
    // 1. Fetch system status
    api.getStatus()
      .then(setStatus)
      .catch((err) => console.warn('Could not reach daemon status:', err));

    // 2. Connect WebSocket
    wsService.connect();
    const unsubStatus = wsService.onStatusChange(setWsConnected);

    const playNotificationSound = () => {
      try {
        const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        if (!AudioContextClass) return;
        const audioCtx = new AudioContextClass();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); // D5
        osc.frequency.exponentialRampToValueAtTime(880, audioCtx.currentTime + 0.15); // A5
        gain.gain.setValueAtTime(0.12, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.35);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.35);
      } catch {
        // Ignored if audio autoplay is restricted
      }
    };

    const unsubEvents = wsService.subscribe((event: WebSocketEvent) => {
      if (event.type === 'reminder_fired' || event.type === 'notification') {
        const payload = (event.data || {}) as Record<string, unknown>;
        const title =
          (payload.title as string) ||
          (event.type === 'reminder_fired' ? 'Reminder Triggered' : 'Notification');
        const message =
          (payload.message as string) || (payload.output as string) || 'No details provided.';
        const notifId = `notif_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`;

        playNotificationSound();
        setNotifications((prev) => [
          ...prev,
          {
            id: notifId,
            title,
            message,
            kind: event.type,
            timestamp: Date.now(),
          },
        ]);
      }
    });

    // 3. Global Ctrl+K shortcut
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsCommandPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleGlobalKeyDown);

    return () => {
      unsubStatus();
      unsubEvents();
      wsService.disconnect();
      window.removeEventListener('keydown', handleGlobalKeyDown);
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
      case 'tasks':
        return <TasksPage />;
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

        {/* Floating Notification Toasts */}
        {notifications.length > 0 && (
          <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-auto">
            {notifications.map((n) => (
              <div
                key={n.id}
                className="bg-surface/95 backdrop-blur-md border border-accent/40 shadow-xl rounded-xl p-3.5 flex items-start gap-3 transition-all animate-in slide-in-from-top-2"
              >
                <div className="p-2 rounded-lg bg-accent/15 text-accent mt-0.5">
                  <Bell className="w-4 h-4 animate-bounce" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-xs text-text flex items-center justify-between">
                    <span className="truncate">{n.title}</span>
                    <button
                      onClick={() => setNotifications((prev) => prev.filter((x) => x.id !== n.id))}
                      className="text-muted hover:text-text p-0.5 rounded ml-2"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div className="text-xs text-muted mt-1 leading-relaxed break-words">
                    {n.message}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
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
