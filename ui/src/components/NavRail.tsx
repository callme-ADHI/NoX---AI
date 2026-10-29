import React from 'react';
import {
  MessageSquare,
  Newspaper,
  Calendar,
  Database,
  Activity,
  Settings as SettingsIcon,
  Wifi,
  WifiOff,
  ListTodo,
  KeyRound,
} from 'lucide-react';
import { SystemStatus } from '../types';

export type TabType = 'chat' | 'briefings' | 'jobs' | 'tasks' | 'keys' | 'memory' | 'activity' | 'settings';

interface NavRailProps {
  activeTab: TabType;
  onTabChange: (tab: TabType) => void;
  status: SystemStatus | null;
  wsConnected: boolean;
}

export const NavRail: React.FC<NavRailProps> = ({
  activeTab,
  onTabChange,
  status,
  wsConnected,
}) => {
  const tabs: { id: TabType; label: string; icon: React.FC<{ className?: string }> }[] = [
    { id: 'chat', label: 'Chat', icon: MessageSquare },
    { id: 'briefings', label: 'Briefings', icon: Newspaper },
    { id: 'jobs', label: 'Jobs', icon: Calendar },
    { id: 'tasks', label: 'Tasks', icon: ListTodo },
    { id: 'keys', label: 'KeyPool', icon: KeyRound },
    { id: 'memory', label: 'Memory', icon: Database },
    { id: 'activity', label: 'Activity', icon: Activity },
    { id: 'settings', label: 'Settings', icon: SettingsIcon },
  ];

  const getStatusColor = () => {
    if (!status || !wsConnected) return 'bg-danger';
    if (!status.ollama_reachable) return 'bg-warning';
    return 'bg-success';
  };

  const getStatusLabel = () => {
    if (!wsConnected) return 'WebSocket Disconnected';
    if (!status) return 'Checking status...';
    if (!status.ollama_reachable) return 'Ollama degraded';
    return 'Daemon Ready';
  };

  return (
    <aside className="w-16 md:w-20 bg-surface/80 backdrop-blur-xl border-r border-border flex flex-col items-center py-4 select-none z-20 shrink-0">
      {/* Brand logo */}
      <div className="mb-6 flex flex-col items-center">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-b from-white/10 to-transparent border border-white/20 shadow-glass flex items-center justify-center text-accent font-bold text-lg">
          <span className="drop-shadow-[0_0_8px_rgba(56,189,248,0.7)]">⚡</span>
        </div>
        <span className="text-[10px] font-mono tracking-widest text-muted mt-1.5 uppercase font-medium">
          NoX
        </span>
      </div>

      {/* Navigation Icons */}
      <nav className="flex-1 flex flex-col gap-2 w-full px-2">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              title={tab.label}
              className={`flex flex-col items-center justify-center py-2.5 px-1 rounded-xl transition-all duration-200 group relative ${
                isActive
                  ? 'bg-white/[0.08] text-white border border-white/15 shadow-glass font-medium'
                  : 'text-muted hover:text-white hover:bg-white/[0.04]'
              }`}
            >
              <Icon className={`w-5 h-5 mb-1 transition-transform group-hover:scale-105 ${isActive ? 'text-accent' : ''}`} />
              <span className="text-[10px] tracking-tight">{tab.label}</span>
              {isActive && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 bg-accent rounded-r shadow-[0_0_10px_rgba(56,189,248,0.8)]" />
              )}
            </button>
          );
        })}
      </nav>

      {/* Health Indicator */}
      <div className="mt-auto pt-4 flex flex-col items-center border-t border-border/50 w-full px-2">
        <div
          className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-white/[0.05] border border-transparent hover:border-white/10 transition-all cursor-pointer group"
          title={getStatusLabel()}
          onClick={() => onTabChange('activity')}
        >
          <span className={`w-2 h-2 rounded-full ${getStatusColor()} animate-pulse shadow-[0_0_8px_currentColor]`} />
          <span className="sr-only">{getStatusLabel()}</span>
          {wsConnected ? (
            <Wifi className="w-3.5 h-3.5 text-muted group-hover:text-white transition-colors" />
          ) : (
            <WifiOff className="w-3.5 h-3.5 text-danger" />
          )}
        </div>
      </div>
    </aside>
  );
};
