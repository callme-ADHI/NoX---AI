import React, { useEffect, useState } from 'react';
import {
  Plug,
  RefreshCw,
  Trash2,
  Mail,
  Calendar as CalendarIcon,
  Rss,
  MessageSquare,
  CheckCircle2,
  AlertCircle,
  Terminal,
} from 'lucide-react';
import { api } from '../services/api';
import { ConnectorInfo } from '../types';
import { DeleteConfirmModal } from '../components/DeleteConfirmModal';

export const ConnectorsPage: React.FC = () => {
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [syncingName, setSyncingName] = useState<string | null>(null);
  const [disconnectTarget, setDisconnectTarget] = useState<string | null>(null);
  const [disconnectLoading, setDisconnectLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const loadConnectors = async () => {
    try {
      setLoading(true);
      const data = await api.getConnectors();
      setConnectors(data);
    } catch (err) {
      console.error('Failed to load connectors:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConnectors();
  }, []);

  const handleSync = async (name: string) => {
    try {
      setSyncingName(name);
      setMessage(null);
      const res = await api.syncConnector(name);
      setMessage({
        text: `Synced ${name} successfully (${JSON.stringify(res.stats || {})})`,
        type: 'success',
      });
      await loadConnectors();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setMessage({ text: `Failed to sync ${name}: ${msg}`, type: 'error' });
    } finally {
      setSyncingName(null);
    }
  };

  const handleDisconnect = async () => {
    if (!disconnectTarget) return;
    try {
      setDisconnectLoading(true);
      await api.disconnectConnector(disconnectTarget);
      setMessage({ text: `Disconnected ${disconnectTarget} and cleared secrets.`, type: 'success' });
      setDisconnectTarget(null);
      await loadConnectors();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setMessage({ text: `Failed to disconnect ${disconnectTarget}: ${msg}`, type: 'error' });
    } finally {
      setDisconnectLoading(false);
    }
  };

  const getConnectorIcon = (type: string, name: string) => {
    if (type === 'mail' || name.includes('mail') || name.includes('imap')) return Mail;
    if (type === 'calendar' || name.includes('calendar')) return CalendarIcon;
    if (type === 'feeds' || name.includes('feed')) return Rss;
    if (type === 'social' || name.includes('telegram') || name.includes('slack')) return MessageSquare;
    return Plug;
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-5xl mx-auto w-full"
      tabIndex={0}
      aria-label="Connectors panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Plug className="w-5 h-5 text-accent" />
            Connectors & Ingestion
          </h1>
          <p className="text-xs text-muted mt-1">
            Manage Gmail, IMAP, Calendar, Channels, and Feeds with zero-cloud local privacy.
          </p>
        </div>

        <button
          onClick={loadConnectors}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Status banner */}
      {message && (
        <div
          className={`p-3 mb-6 rounded-xl border text-xs flex items-center justify-between animate-in fade-in ${
            message.type === 'success'
              ? 'bg-success/10 border-success/30 text-success'
              : 'bg-danger/10 border-danger/30 text-danger'
          }`}
        >
          <span>{message.text}</span>
          <button
            onClick={() => setMessage(null)}
            className="text-[11px] font-semibold underline ml-4 hover:opacity-80"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Connectors Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {connectors.map((connector) => {
          const Icon = getConnectorIcon(connector.type, connector.name);
          const isSyncing = syncingName === connector.name;
          const isHealthy = connector.healthy;

          return (
            <div
              key={connector.name}
              className="p-5 rounded-xl bg-surface border border-border shadow-glass flex flex-col justify-between transition-all hover:border-white/20"
            >
              <div>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-white/[0.05] border border-white/10 text-accent">
                      <Icon className="w-5 h-5" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-text capitalize">
                        {connector.name}
                      </h3>
                      <span className="text-[11px] font-mono text-muted uppercase">
                        Type: {connector.type}
                      </span>
                    </div>
                  </div>

                  <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-medium ${
                      isHealthy
                        ? 'bg-success/10 text-success border border-success/20'
                        : 'bg-warning/10 text-warning border border-warning/20'
                    }`}
                  >
                    {isHealthy ? (
                      <CheckCircle2 className="w-3 h-3" />
                    ) : (
                      <AlertCircle className="w-3 h-3" />
                    )}
                    {connector.status}
                  </span>
                </div>

                <div className="space-y-1.5 py-2 text-xs border-t border-border/40 text-muted">
                  <div className="flex justify-between">
                    <span>Items Ingested:</span>
                    <span className="font-mono text-text font-medium">{connector.item_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Last Synced:</span>
                    <span className="font-mono text-text">
                      {connector.last_sync_at
                        ? new Date(connector.last_sync_at).toLocaleString()
                        : 'Never'}
                    </span>
                  </div>
                  {connector.error && (
                    <div className="text-danger text-[11px] mt-1 break-all">
                      {connector.error}
                    </div>
                  )}
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-end gap-2 pt-3 mt-3 border-t border-border/40">
                <button
                  onClick={() => setDisconnectTarget(connector.name)}
                  className="px-2.5 py-1.5 rounded-lg text-danger hover:bg-danger/10 border border-transparent hover:border-danger/20 text-xs font-medium transition-colors flex items-center gap-1"
                  title="Disconnect & revoke credentials"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Disconnect
                </button>
                <button
                  onClick={() => handleSync(connector.name)}
                  disabled={isSyncing}
                  className="px-3 py-1.5 rounded-lg bg-white/[0.08] hover:bg-white/[0.12] text-white border border-white/10 text-xs font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin text-accent' : ''}`} />
                  {isSyncing ? 'Syncing...' : 'Sync Now'}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* CLI Setup Card */}
      <div className="p-5 rounded-xl bg-surface/50 border border-border flex flex-col gap-3">
        <div className="flex items-center gap-2 text-text font-semibold text-xs">
          <Terminal className="w-4 h-4 text-accent" />
          <span>Need to connect new services? Pair via CLI</span>
        </div>
        <p className="text-[11px] text-muted">
          All connector authentications use local-only secure secret storage and PKCE OAuth / App Passwords:
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] font-mono text-text/90">
          <div className="p-2 rounded bg-black/40 border border-border/60">
            <span className="text-muted"># Connect Gmail (PKCE):</span>
            <div className="text-accent mt-0.5">nx connect gmail</div>
          </div>
          <div className="p-2 rounded bg-black/40 border border-border/60">
            <span className="text-muted"># Connect IMAP SSL:</span>
            <div className="text-accent mt-0.5">nx connect imap</div>
          </div>
          <div className="p-2 rounded bg-black/40 border border-border/60">
            <span className="text-muted"># Pair Telegram Channel:</span>
            <div className="text-accent mt-0.5">nx channel telegram pair</div>
          </div>
          <div className="p-2 rounded bg-black/40 border border-border/60">
            <span className="text-muted"># Add Public RSS/Atom Feed:</span>
            <div className="text-accent mt-0.5">nx feed add &lt;feed_url&gt;</div>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      <DeleteConfirmModal
        isOpen={!!disconnectTarget}
        title={`Disconnect ${disconnectTarget}?`}
        message={`This will revoke and delete all stored tokens and secrets for '${disconnectTarget}'. Future sync operations will fail until re-authenticated.`}
        confirmLabel="Disconnect"
        isDangerous={true}
        loading={disconnectLoading}
        onConfirm={handleDisconnect}
        onCancel={() => setDisconnectTarget(null)}
      />
    </div>
  );
};
