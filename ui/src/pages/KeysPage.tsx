import React, { useEffect, useState } from 'react';
import {
  KeyRound,
  RefreshCw,
  Zap,
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  ShieldAlert,
} from 'lucide-react';
import { api } from '../services/api';

interface KeyRow {
  key_id: string;
  masked: string;
  status: string;
  in_flight: number;
  rpm_limit: number;
  tokens_left: number;
  ewma_latency_ms: number;
  total_requests_today: number;
  rate_limit_count_today: number;
  pinned_job_id: string | null;
  cooldown_until: number | null;
}

interface TestResult {
  key_id: string;
  masked: string;
  status_code: number;
  latency_ms: number;
  ok: boolean;
  error?: string;
}

export const KeysPage: React.FC = () => {
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  useEffect(() => {
    loadKeys();
  }, []);

  const loadKeys = async () => {
    setLoading(true);
    try {
      const data = await api.getKeysStatus();
      setKeys(data);
    } catch (err) {
      console.error('Failed to load key pool status:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleTestKeys = async () => {
    setTesting(true);
    try {
      const results = await api.testKeys();
      const map: Record<string, TestResult> = {};
      results.forEach((r) => {
        map[r.key_id] = r;
      });
      setTestResults(map);
      await loadKeys();
    } catch (err) {
      console.error('Failed to test keys:', err);
    } finally {
      setTesting(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'ACTIVE':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-success/15 text-success border border-success/30">
            <CheckCircle2 className="w-3 h-3" />
            ACTIVE
          </span>
        );
      case 'COOLDOWN':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-warning/15 text-warning border border-warning/30">
            <Clock className="w-3 h-3" />
            COOLDOWN
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-danger/15 text-danger border border-danger/30">
            <AlertTriangle className="w-3 h-3" />
            {status}
          </span>
        );
    }
  };

  return (
    <div className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <KeyRound className="w-5 h-5 text-accent" />
            NVIDIA API Key Pool
          </h1>
          <p className="text-xs text-muted mt-1">
            Dynamic allocator with EWMA latency load-balancing, rate limit isolation, and job pinning.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadKeys}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface hover:bg-raised text-text text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={handleTestKeys}
            disabled={testing}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors shadow-sm"
          >
            <Zap className={`w-3.5 h-3.5 ${testing ? 'animate-spin' : 'fill-current'}`} />
            {testing ? 'Testing Keys...' : 'Test All Keys Live'}
          </button>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div className="p-4 rounded-xl bg-surface/70 backdrop-blur-md border border-border flex flex-col gap-1">
          <span className="text-[11px] text-muted font-medium">Configured Keys</span>
          <span className="text-2xl font-bold text-text">{keys.length}</span>
          <span className="text-[10px] text-muted">Managed via secrets.env (0600)</span>
        </div>

        <div className="p-4 rounded-xl bg-surface/70 backdrop-blur-md border border-border flex flex-col gap-1">
          <span className="text-[11px] text-muted font-medium">Active Keys</span>
          <span className="text-2xl font-bold text-success">
            {keys.filter((k) => k.status === 'ACTIVE').length}
          </span>
          <span className="text-[10px] text-muted">Ready for allocation</span>
        </div>

        <div className="p-4 rounded-xl bg-surface/70 backdrop-blur-md border border-border flex flex-col gap-1">
          <span className="text-[11px] text-muted font-medium">In Flight Requests</span>
          <span className="text-2xl font-bold text-accent">
            {keys.reduce((acc, k) => acc + (k.in_flight || 0), 0)}
          </span>
          <span className="text-[10px] text-muted">Concurrent active leases</span>
        </div>

        <div className="p-4 rounded-xl bg-surface/70 backdrop-blur-md border border-border flex flex-col gap-1">
          <span className="text-[11px] text-muted font-medium">Today's Requests</span>
          <span className="text-2xl font-bold text-text">
            {keys.reduce((acc, k) => acc + (k.total_requests_today || 0), 0)}
          </span>
          <span className="text-[10px] text-muted">
            {keys.reduce((acc, k) => acc + (k.rate_limit_count_today || 0), 0)} rate limit (429) events
          </span>
        </div>
      </div>

      {/* Keys Table */}
      <div className="rounded-xl bg-surface/80 backdrop-blur-md border border-border shadow-sm overflow-hidden mb-6">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold text-text">Pool Allocator State</h2>
          </div>
          <span className="text-[11px] text-muted font-mono">
            Algorithm: EWMA-Latency Weighted Least-Connections
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-raised/50 text-muted uppercase tracking-wider font-semibold border-b border-border text-[10px]">
              <tr>
                <th className="px-4 py-3">Key ID</th>
                <th className="px-4 py-3">Masked Secret</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">In-Flight</th>
                <th className="px-4 py-3 text-right">RPM Limit</th>
                <th className="px-4 py-3 text-right">Tokens Left</th>
                <th className="px-4 py-3 text-right">EWMA Latency</th>
                <th className="px-4 py-3 text-right">Requests Today</th>
                <th className="px-4 py-3 text-right">429s</th>
                <th className="px-4 py-3">Allocation / Pin</th>
                <th className="px-4 py-3 text-right">Live Probe</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {loading && keys.length === 0 ? (
                <tr>
                  <td colSpan={11} className="p-8 text-center text-muted">
                    Loading key pool status...
                  </td>
                </tr>
              ) : keys.length === 0 ? (
                <tr>
                  <td colSpan={11} className="p-8 text-center text-muted">
                    No keys found. Add NVIDIA_API_KEY_1..5 in ~/.config/nox/secrets.env
                  </td>
                </tr>
              ) : (
                keys.map((k) => {
                  const test = testResults[k.key_id];
                  return (
                    <tr key={k.key_id} className="hover:bg-raised/30 transition-colors">
                      <td className="px-4 py-3 font-mono font-semibold text-accent">{k.key_id}</td>
                      <td className="px-4 py-3 font-mono text-text">{k.masked}</td>
                      <td className="px-4 py-3">{getStatusBadge(k.status)}</td>
                      <td className="px-4 py-3 text-right font-mono text-text">{k.in_flight}</td>
                      <td className="px-4 py-3 text-right font-mono text-muted">{k.rpm_limit}/min</td>
                      <td className="px-4 py-3 text-right font-mono text-muted">
                        {k.tokens_left.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-text">
                        {k.ewma_latency_ms ? `${k.ewma_latency_ms.toFixed(0)} ms` : '—'}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-text">
                        {k.total_requests_today}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-danger">
                        {k.rate_limit_count_today}
                      </td>
                      <td className="px-4 py-3">
                        {k.pinned_job_id ? (
                          <span className="px-2 py-0.5 rounded text-[10px] bg-accent/15 text-accent border border-accent/30 font-mono">
                            Pinned: {k.pinned_job_id}
                          </span>
                        ) : (
                          <span className="text-muted text-[11px]">Free pool</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {test ? (
                          test.ok ? (
                            <span className="inline-flex items-center gap-1 font-mono text-success text-[11px]">
                              200 OK ({test.latency_ms}ms)
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 font-mono text-danger text-[11px]">
                              ERR {test.status_code || test.error}
                            </span>
                          )
                        ) : (
                          <span className="text-muted text-[11px]">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Allocation Modes Documentation Card */}
      <div className="p-5 rounded-xl bg-surface/60 backdrop-blur-md border border-border shadow-sm flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-accent" />
          <h3 className="text-xs font-semibold text-text uppercase tracking-wider">
            Key Allocation Modes & Priority Classes
          </h3>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-xs">
          <div className="p-3 rounded-lg bg-raised/40 border border-border/50">
            <span className="font-semibold text-accent block mb-1">INTERACTIVE</span>
            <p className="text-muted text-[11px]">
              User chat turns take priority lease on lowest-EWMA key with in-flight headroom.
            </p>
          </div>
          <div className="p-3 rounded-lg bg-raised/40 border border-border/50">
            <span className="font-semibold text-accent block mb-1">PINNED</span>
            <p className="text-muted text-[11px]">
              Multi-step jobs pin to a single key for consistent caching and rate isolation.
            </p>
          </div>
          <div className="p-3 rounded-lg bg-raised/40 border border-border/50">
            <span className="font-semibold text-accent block mb-1">WIDE (PARALLEL)</span>
            <p className="text-muted text-[11px]">
              Briefing crawler fans out across 4–5 keys concurrently for fast topic extraction.
            </p>
          </div>
          <div className="p-3 rounded-lg bg-raised/40 border border-border/50">
            <span className="font-semibold text-accent block mb-1">MULTI-JOB SAFE</span>
            <p className="text-muted text-[11px]">
              Background tasks yield instantly if interactive user chat needs headroom.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
