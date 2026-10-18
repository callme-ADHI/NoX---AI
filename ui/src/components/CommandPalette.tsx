import React, { useEffect, useState } from 'react';
import {
  Search,
  MessageSquare,
  Newspaper,
  Calendar,
  Database,
  Activity,
  Settings,
  Plus,
  RefreshCw,
  X,
} from 'lucide-react';
import { TabType } from './NavRail';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectTab: (tab: TabType) => void;
  onNewChat: () => void;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({
  isOpen,
  onClose,
  onSelectTab,
  onNewChat,
}) => {
  const [query, setQuery] = useState('');

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        if (isOpen) onClose();
        else {
          setQuery('');
        }
      } else if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const commands = [
    {
      id: 'new-chat',
      title: 'New Conversation',
      subtitle: 'Start a fresh chat with NoX',
      icon: Plus,
      action: () => {
        onNewChat();
        onSelectTab('chat');
        onClose();
      },
    },
    {
      id: 'tab-chat',
      title: 'Go to Chat',
      subtitle: 'Conversations & streaming agent execution',
      icon: MessageSquare,
      action: () => {
        onSelectTab('chat');
        onClose();
      },
    },
    {
      id: 'tab-briefings',
      title: 'Go to Briefings',
      subtitle: 'Daily cybersecurity & tech briefings',
      icon: Newspaper,
      action: () => {
        onSelectTab('briefings');
        onClose();
      },
    },
    {
      id: 'tab-jobs',
      title: 'Go to Jobs',
      subtitle: 'Manage scheduled tasks, cron jobs & reminders',
      icon: Calendar,
      action: () => {
        onSelectTab('jobs');
        onClose();
      },
    },
    {
      id: 'tab-memory',
      title: 'Go to Memory',
      subtitle: 'Browse durable facts and interaction episodes',
      icon: Database,
      action: () => {
        onSelectTab('memory');
        onClose();
      },
    },
    {
      id: 'tab-activity',
      title: 'Go to Activity & Audit',
      subtitle: 'System health, model usage & audit log',
      icon: Activity,
      action: () => {
        onSelectTab('activity');
        onClose();
      },
    },
    {
      id: 'tab-settings',
      title: 'Go to Settings',
      subtitle: 'Configure model roles, themes and notifications',
      icon: Settings,
      action: () => {
        onSelectTab('settings');
        onClose();
      },
    },
    {
      id: 'reload-page',
      title: 'Reload UI',
      subtitle: 'Refresh application and reconnect WebSocket',
      icon: RefreshCw,
      action: () => {
        window.location.reload();
      },
    },
  ];

  const filtered = commands.filter(
    (c) =>
      c.title.toLowerCase().includes(query.toLowerCase()) ||
      c.subtitle.toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-xl bg-surface border border-border rounded-xl shadow-2xl overflow-hidden flex flex-col animate-in fade-in zoom-in-95 duration-100">
        {/* Search header */}
        <div className="flex items-center px-4 py-3 border-b border-border/80">
          <Search className="w-5 h-5 text-muted mr-3 shrink-0" />
          <input
            autoFocus
            type="text"
            placeholder="Type a command or search..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full bg-transparent text-text text-sm placeholder:text-muted focus:outline-none"
          />
          <button
            onClick={onClose}
            className="text-muted hover:text-text p-1 rounded-md transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Command list */}
        <div className="max-h-80 overflow-y-auto p-2 divide-y divide-border/20">
          {filtered.length === 0 ? (
            <div className="py-8 text-center text-muted text-sm">
              No matching commands found.
            </div>
          ) : (
            filtered.map((cmd) => {
              const Icon = cmd.icon;
              return (
                <button
                  key={cmd.id}
                  onClick={cmd.action}
                  className="w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-raised transition-colors group"
                >
                  <div className="p-2 rounded-md bg-raised text-accent border border-border/50 group-hover:border-accent/40 transition-colors">
                    <Icon className="w-4 h-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-text">{cmd.title}</div>
                    <div className="text-xs text-muted truncate">{cmd.subtitle}</div>
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* Footer shortcuts */}
        <div className="bg-raised/40 px-4 py-2 border-t border-border flex items-center justify-between text-[11px] text-muted">
          <span>Navigation Shortcuts</span>
          <div className="flex items-center gap-2">
            <kbd className="px-1.5 py-0.5 rounded bg-surface border border-border font-mono">
              Esc
            </kbd>
            <span>to close</span>
          </div>
        </div>
      </div>
    </div>
  );
};
