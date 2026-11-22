import React, { useEffect, useState } from 'react';
import {
  ShieldCheck,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Copy,
  Check,
  ArrowRight,
} from 'lucide-react';
import { api } from '../services/api';
import { AuditChainEvent } from '../types';

export const AuditPage: React.FC = () => {
  const [events, setEvents] = useState<AuditChainEvent[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [verifying, setVerifying] = useState<boolean>(false);
  const [verificationResult, setVerificationResult] = useState<{
    verified: boolean;
    total_events: number;
    message: string;
  } | null>(null);
  const [copiedHash, setCopiedHash] = useState<string | null>(null);

  const loadAuditChain = async () => {
    try {
      setLoading(true);
      const data = await api.getAuditChain(50);
      setEvents(data);
    } catch (err) {
      console.error('Failed to load audit chain:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAuditChain();
  }, []);

  const handleVerifyChain = async () => {
    try {
      setVerifying(true);
      const res = await api.verifyAuditChain();
      setVerificationResult(res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setVerificationResult({
        verified: false,
        total_events: 0,
        message: `Verification check failed: ${msg}`,
      });
    } finally {
      setVerifying(false);
    }
  };

  const handleCopyHash = (hash: string) => {
    navigator.clipboard.writeText(hash);
    setCopiedHash(hash);
    setTimeout(() => setCopiedHash(null), 1500);
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-5xl mx-auto w-full"
      tabIndex={0}
      aria-label="Audit chain panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-accent" />
            Tamper-Evident Audit Log
          </h1>
          <p className="text-xs text-muted mt-1">
            SHA-256 cryptographic forward-hash chain tracking all safety evaluations, permissions, and tool executions.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleVerifyChain}
            disabled={verifying}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors shadow-sm disabled:opacity-50"
          >
            <ShieldCheck className={`w-3.5 h-3.5 ${verifying ? 'animate-spin' : ''}`} />
            {verifying ? 'Verifying Chain...' : 'Verify Cryptographic Chain'}
          </button>
          <button
            onClick={loadAuditChain}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Verification Result Banner */}
      {verificationResult && (
        <div
          className={`p-4 mb-6 rounded-xl border text-xs flex items-center justify-between animate-in fade-in ${
            verificationResult.verified
              ? 'bg-success/10 border-success/30 text-success'
              : 'bg-danger/10 border-danger/30 text-danger'
          }`}
        >
          <div className="flex items-center gap-3">
            {verificationResult.verified ? (
              <CheckCircle2 className="w-5 h-5 shrink-0" />
            ) : (
              <AlertTriangle className="w-5 h-5 shrink-0" />
            )}
            <div>
              <div className="font-bold text-sm">
                {verificationResult.verified
                  ? 'Audit Log Integrity Verified'
                  : 'Integrity Violation Detected'}
              </div>
              <p className="text-[11px] opacity-90 mt-0.5">
                {verificationResult.message} ({verificationResult.total_events} events verified)
              </p>
            </div>
          </div>

          <button
            onClick={() => setVerificationResult(null)}
            className="text-[11px] font-semibold underline hover:opacity-80 ml-4"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Events Table / Timeline */}
      {loading && events.length === 0 ? (
        <div className="p-8 text-center text-muted text-xs flex items-center justify-center gap-2">
          <RefreshCw className="w-4 h-4 animate-spin text-accent" />
          Loading audit events...
        </div>
      ) : events.length === 0 ? (
        <div className="p-12 text-center rounded-xl bg-surface/50 border border-border text-muted text-xs">
          No audit events recorded yet.
        </div>
      ) : (
        <div className="space-y-3 mb-8">
          {events.map((event) => (
            <div
              key={event.id}
              className="p-4 rounded-xl bg-surface border border-border shadow-glass hover:border-white/20 transition-all text-xs"
            >
              <div className="flex items-start justify-between gap-4 mb-2">
                <div className="flex items-center gap-2.5 flex-wrap">
                  <span className="font-mono text-accent font-bold px-2 py-0.5 rounded bg-white/[0.05] border border-white/10">
                    #{event.sequence_num}
                  </span>
                  <span className="font-bold text-text">{event.action}</span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono uppercase font-semibold ${
                      event.decision === 'allow' || event.decision === 'approved'
                        ? 'bg-success/15 text-success border border-success/30'
                        : 'bg-danger/15 text-danger border border-danger/30'
                    }`}
                  >
                    {event.decision}
                  </span>
                  <span className="text-muted font-mono text-[11px]">
                    by {event.actor}
                  </span>
                </div>

                <span className="text-[11px] text-muted font-mono shrink-0">
                  {new Date(event.created_at).toLocaleString()}
                </span>
              </div>

              <div className="text-text/80 leading-relaxed mb-3 font-sans">
                {event.summary || '(No summary recorded)'}
              </div>

              {/* Hash Chain Linkages */}
              <div className="p-2.5 rounded-lg bg-black/40 border border-border/60 flex items-center justify-between gap-3 text-[11px] font-mono">
                <div className="flex items-center gap-2 truncate">
                  <span className="text-muted">Prev:</span>
                  <span className="text-text/70 truncate max-w-[120px]">
                    {event.prev_hash}
                  </span>
                  <ArrowRight className="w-3 h-3 text-muted shrink-0" />
                  <span className="text-muted">Current:</span>
                  <span className="text-accent truncate max-w-[160px]">
                    {event.current_hash}
                  </span>
                </div>

                <button
                  onClick={() => handleCopyHash(event.current_hash)}
                  className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors shrink-0"
                  title="Copy full SHA-256 hash"
                >
                  {copiedHash === event.current_hash ? (
                    <Check className="w-3.5 h-3.5 text-success" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
