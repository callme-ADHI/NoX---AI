import React, { useEffect, useState } from 'react';
import {
  Bell,
  CheckCheck,
  X,
  AlertCircle,
  Info,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { NotificationItem, WebSocketEvent } from '../types';
import { api } from '../services/api';
import { wsService } from '../services/websocket';

export interface NotificationCenterProps {
  isOpen: boolean;
  onClose: () => void;
  onUnreadCountChange?: (count: number) => void;
}

export const NotificationCenter: React.FC<NotificationCenterProps> = ({
  isOpen,
  onClose,
  onUnreadCountChange,
}) => {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    loadNotifications();
  }, []);

  // Listen to WebSocket events for real-time notification push
  useEffect(() => {
    const unsub = wsService.subscribe((event: WebSocketEvent) => {
      if (event.type === 'notification' || event.type === 'reminder_fired' || event.type === 'job_finished') {
        loadNotifications();
      }
    });
    return unsub;
  }, []);

  const loadNotifications = async () => {
    setLoading(true);
    try {
      const data = await api.getNotifications();
      setNotifications(data);
      const unread = data.filter((n) => !n.read).length;
      if (onUnreadCountChange) onUnreadCountChange(unread);
    } catch (err) {
      console.error('Failed to load notifications:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleMarkAllRead = async () => {
    setClearing(true);
    try {
      await api.clearNotifications();
      setNotifications((prev) => prev.map((n) => ({ ...n, read: 1 })));
      if (onUnreadCountChange) onUnreadCountChange(0);
    } catch (err) {
      console.error('Failed to mark all as read:', err);
    } finally {
      setClearing(false);
    }
  };

  const handleMarkRead = async (id: number) => {
    try {
      await api.markNotificationRead(id);
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, read: 1 } : n))
      );
      const remainingUnread = notifications.filter((n) => !n.read && n.id !== id).length;
      if (onUnreadCountChange) onUnreadCountChange(remainingUnread);
    } catch (err) {
      console.error('Failed to mark notification read:', err);
    }
  };

  if (!isOpen) return null;

  const unreadCount = notifications.filter((n) => !n.read).length;

  const getLevelIcon = (level: string) => {
    switch (level) {
      case 'error':
        return <AlertCircle className="w-4 h-4 text-danger" />;
      case 'warning':
        return <AlertTriangle className="w-4 h-4 text-warning" />;
      case 'success':
        return <CheckCircle2 className="w-4 h-4 text-success" />;
      default:
        return <Info className="w-4 h-4 text-accent" />;
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Notification Center"
      className="fixed inset-y-0 right-0 w-full max-w-sm bg-surface border-l border-border shadow-2xl z-50 flex flex-col animate-in slide-in-from-right duration-150 text-xs text-text"
    >
      {/* Header */}
      <div className="p-4 border-b border-border/80 flex items-center justify-between bg-raised/30">
        <div className="flex items-center gap-2">
          <Bell className="w-4 h-4 text-accent" />
          <h3 className="text-sm font-bold text-text">Notifications</h3>
          {unreadCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-accent text-black font-mono">
              {unreadCount}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {unreadCount > 0 && (
            <button
              onClick={handleMarkAllRead}
              disabled={clearing}
              title="Mark all as read"
              className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors flex items-center gap-1 text-[11px]"
            >
              <CheckCheck className="w-3.5 h-3.5 text-accent" />
              <span>Mark all read</span>
            </button>
          )}
          <button
            onClick={onClose}
            className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors"
            aria-label="Close notifications"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
        {loading && notifications.length === 0 ? (
          <div className="p-8 text-center text-xs text-muted">Loading notifications...</div>
        ) : notifications.length === 0 ? (
          <div className="p-8 text-center text-xs text-muted flex flex-col items-center gap-2">
            <Bell className="w-8 h-8 text-border" />
            <span>No notifications yet.</span>
          </div>
        ) : (
          notifications.map((item) => (
            <div
              key={item.id}
              onClick={() => !item.read && handleMarkRead(item.id)}
              className={`p-3 rounded-lg border transition-all cursor-pointer flex flex-col gap-1.5 ${
                item.read
                  ? 'bg-background/60 border-border/50 text-muted opacity-80'
                  : 'bg-background border-border text-text shadow-sm hover:border-accent/50'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  {getLevelIcon(item.level)}
                  <span className="font-semibold text-xs text-text">{item.title}</span>
                </div>
                {!item.read && (
                  <span className="w-2 h-2 rounded-full bg-accent shrink-0 mt-1 shadow-[0_0_6px_rgba(56,189,248,0.8)]" />
                )}
              </div>

              <p className="text-[11px] text-text/80 leading-relaxed font-sans pl-6 break-words">
                {item.message}
              </p>

              <div className="flex items-center justify-between text-[10px] text-muted font-mono pl-6 pt-1">
                <span>{new Date(item.created_at).toLocaleString()}</span>
                {item.job_id && <span className="truncate max-w-[100px]">job: {item.job_id}</span>}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
