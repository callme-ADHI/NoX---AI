import React, { useEffect, useState } from 'react';
import {
  ShieldAlert,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Clock,
  Key,
  ShieldCheck,
  AlertTriangle,
} from 'lucide-react';
import { api } from '../services/api';
import { ScopedApproval } from '../types';

export const ApprovalsPage: React.FC = () => {
  const [approvals, setApprovals] = useState<ScopedApproval[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [approvedTokens, setApprovedTokens] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const loadApprovals = async () => {
    try {
      setLoading(true);
      const data = await api.getPendingApprovals();
      setApprovals(data);
    } catch (err) {
      console.error('Failed to load pending approvals:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadApprovals();
  }, []);

  const handleResolve = async (id: string, decision: 'approve' | 'deny') => {
    try {
      setResolvingId(id);
      setMessage(null);
      const res = await api.resolveScopedApproval(id, decision);
      if (res.token) {
        setApprovedTokens((prev) => ({ ...prev, [id]: res.token! }));
        setMessage({
          text: `Approval ${id} granted. Single-use execution token generated.`,
          type: 'success',
        });
      } else {
        setMessage({
          text: `Approval ${id} denied. Action blocked.`,
          type: 'error',
        });
      }
      await loadApprovals();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setMessage({ text: `Failed to resolve approval: ${msg}`, type: 'error' });
    } finally {
      setResolvingId(null);
    }
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-5xl mx-auto w-full"
      tabIndex={0}
      aria-label="Approvals center panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-accent" />
            Human Approvals Center
          </h1>
          <p className="text-xs text-muted mt-1">
            Review and gate high-impact write operations with single-use cryptographic approval tokens.
          </p>
        </div>

        <button
          onClick={loadApprovals}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Banner */}
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

      {/* Approvals List */}
      {loading && approvals.length === 0 ? (
        <div className="p-8 text-center text-muted text-xs flex items-center justify-center gap-2">
          <RefreshCw className="w-4 h-4 animate-spin text-accent" />
          Loading pending approvals...
        </div>
      ) : approvals.length === 0 ? (
        <div className="p-12 text-center rounded-xl bg-surface/50 border border-border text-muted text-xs">
          <ShieldCheck className="w-8 h-8 text-success/60 mx-auto mb-2" />
          No pending approvals. All protected actions are cleared or idle.
        </div>
      ) : (
        <div className="space-y-4 mb-8">
          {approvals.map((approval) => {
            const isResolving = resolvingId === approval.id;
            const token = approvedTokens[approval.id];

            return (
              <div
                key={approval.id}
                className="p-5 rounded-xl bg-surface border border-border shadow-glass flex flex-col justify-between hover:border-white/20 transition-all"
              >
                <div>
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="p-2.5 rounded-xl bg-warning/10 border border-warning/20 text-warning">
                        <AlertTriangle className="w-5 h-5" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-bold text-text">
                            {approval.action}
                          </h3>
                          <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase bg-accent/15 text-accent border border-accent/30 font-semibold">
                            Scope: {approval.scope}
                          </span>
                        </div>
                        <span className="text-[11px] font-mono text-muted">
                          ID: {approval.id}
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 text-xs text-muted">
                      <Clock className="w-3.5 h-3.5 text-warning" />
                      <span>Expires: {new Date(approval.expires_at).toLocaleTimeString()}</span>
                    </div>
                  </div>

                  {/* Payload Details */}
                  <div className="my-3 p-3 rounded-lg bg-black/40 border border-border/60">
                    <span className="text-[10px] font-mono text-muted uppercase block mb-1">
                      Action Payload Parameters:
                    </span>
                    <pre className="text-xs text-text/90 font-mono overflow-x-auto">
                      {JSON.stringify(approval.payload, null, 2)}
                    </pre>
                  </div>

                  {/* Generated Token display */}
                  {token && (
                    <div className="mb-3 p-3 rounded-lg bg-success/10 border border-success/30 text-success text-xs font-mono flex items-center justify-between">
                      <div className="flex items-center gap-2 truncate">
                        <Key className="w-4 h-4 shrink-0" />
                        <span>Token: {token}</span>
                      </div>
                      <span className="text-[10px] text-muted">Single-use active</span>
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div className="flex items-center justify-end gap-2 pt-3 border-t border-border/40">
                  <button
                    onClick={() => handleResolve(approval.id, 'deny')}
                    disabled={isResolving}
                    className="px-3 py-1.5 rounded-lg bg-danger/15 hover:bg-danger/25 text-danger border border-danger/30 text-xs font-semibold transition-colors flex items-center gap-1.5 disabled:opacity-50"
                  >
                    <XCircle className="w-3.5 h-3.5" />
                    Deny Action
                  </button>
                  <button
                    onClick={() => handleResolve(approval.id, 'approve')}
                    disabled={isResolving}
                    className="px-3.5 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors flex items-center gap-1.5 disabled:opacity-50 shadow-sm"
                  >
                    <CheckCircle2 className={`w-3.5 h-3.5 ${isResolving ? 'animate-spin' : ''}`} />
                    {isResolving ? 'Processing...' : 'Approve & Issue Token'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
