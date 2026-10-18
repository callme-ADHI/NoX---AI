import React, { useEffect, useState } from 'react';
import {
  Activity,
  Cpu,
  Cloud,
  Database,
  Shield,
  Layers,
  Filter,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { AuditEntry, SystemStatus } from '../types';
import { api } from '../services/api';

export const ActivityPage: React.FC = () => {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([]);
  const [filterTool, setFilterTool] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [sData, aData] = await Promise.all([
        api.getStatus(),
        api.getAuditLog(100),
      ]);
      setStatus(sData);
      setAuditLog(aData);
    } catch (err) {
      console.error('Failed to load activity data:', err);
    } finally {
      setLoading(false);
    }
  };

  const filteredLogs = auditLog.filter((a) =>
    !filterTool || a.tool.toLowerCase().includes(filterTool.toLowerCase())
  );

  return (
    <div className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Activity className="w-5 h-5 text-accent" />
            System Health & Audit Activity
          </h1>
          <p className="text-xs text-muted mt-1">
            Real-time status of local models, vector storage, and full tool execution ledger.
          </p>
        </div>

        <button
          onClick={loadData}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface hover:bg-raised border border-border text-xs text-text transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh Status
        </button>
      </div>

      {/* System Status Cards */}
      {status && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 mb-6">
          {/* Card 1: Ollama & Models */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-muted text-xs mb-2">
              <span className="font-semibold">Local Brain</span>
              <Cpu className="w-4 h-4 text-accent" />
            </div>
            <div className="flex items-center gap-2 mb-1">
              <span
                className={`w-2 h-2 rounded-full ${
                  status.ollama_reachable ? 'bg-success' : 'bg-danger'
                }`}
              />
              <span className="text-sm font-bold text-text">
                {status.ollama_reachable ? 'Ollama Online' : 'Offline'}
              </span>
            </div>
            <div className="text-[11px] text-muted truncate">
              {status.loaded_models.length > 0
                ? `${status.loaded_models.length} model(s) pulled`
                : 'No models found'}
            </div>
          </div>

          {/* Card 2: Cloud Model & RPM */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-muted text-xs mb-2">
              <span className="font-semibold">Cloud Role (Heavy)</span>
              <Cloud className="w-4 h-4 text-sky-400" />
            </div>
            <div className="flex items-center gap-2 mb-1">
              <span
                className={`w-2 h-2 rounded-full ${
                  status.cloud_available ? 'bg-sky-400' : 'bg-muted'
                }`}
              />
              <span className="text-sm font-bold text-text">
                {status.cloud_available ? 'NVIDIA Ready' : 'Unconfigured'}
              </span>
            </div>
            <div className="text-[11px] text-muted">
              Rate limit: 30 RPM (Token bucket)
            </div>
          </div>

          {/* Card 3: Storage & Vector DB */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-muted text-xs mb-2">
              <span className="font-semibold">Database & Vector</span>
              <Database className="w-4 h-4 text-purple-400" />
            </div>
            <div className="text-sm font-bold text-text mb-1">
              {(status.db_size_bytes / (1024 * 1024)).toFixed(2)} MB
            </div>
            <div className="text-[11px] text-muted uppercase font-mono">
              Backend: {status.vector_backend}
            </div>
          </div>

          {/* Card 4: Gate Security */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-muted text-xs mb-2">
              <span className="font-semibold">Gate & Security</span>
              <Shield className="w-4 h-4 text-warning" />
            </div>
            <div className="flex items-center gap-2 mb-1">
              <span
                className={`w-2 h-2 rounded-full ${
                  status.authenticated ? 'bg-success' : 'bg-danger'
                }`}
              />
              <span className="text-sm font-bold text-text">
                {status.authenticated ? 'Authenticated' : 'Locked'}
              </span>
            </div>
            <div className="text-[11px] text-muted font-mono capitalize">
              Mode: {status.gate_mode}
            </div>
          </div>
        </div>
      )}

      {/* Audit Log Table Header */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-text flex items-center gap-2">
          <Layers className="w-4 h-4 text-muted" />
          Tool Execution Audit Ledger
        </h2>

        {/* Filter input */}
        <div className="relative w-48">
          <Filter className="w-3.5 h-3.5 text-muted absolute left-2.5 top-2" />
          <input
            type="text"
            placeholder="Filter by tool..."
            value={filterTool}
            onChange={(e) => setFilterTool(e.target.value)}
            className="w-full bg-surface border border-border rounded-lg pl-8 pr-3 py-1 text-xs text-text placeholder:text-muted focus:outline-none focus:border-accent"
          />
        </div>
      </div>

      {/* Audit Log Table */}
      <div className="flex-1 bg-surface border border-border rounded-xl overflow-hidden shadow-sm flex flex-col">
        <div className="overflow-x-auto flex-1">
          <table className="w-full text-left text-xs">
            <thead className="bg-raised/50 border-b border-border text-muted font-mono uppercase text-[10px]">
              <tr>
                <th className="py-3 px-4">Timestamp</th>
                <th className="py-3 px-4">Tool</th>
                <th className="py-3 px-4">Tier</th>
                <th className="py-3 px-4">Decision</th>
                <th className="py-3 px-4">Duration</th>
                <th className="py-3 px-4">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40 font-mono">
              {filteredLogs.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-muted font-sans text-xs">
                    No audit records logged yet.
                  </td>
                </tr>
              ) : (
                filteredLogs.map((entry) => (
                  <tr key={entry.id} className="hover:bg-raised/30 transition-colors">
                    <td className="py-3 px-4 text-muted text-[11px]">
                      {new Date(entry.ts).toLocaleTimeString()}
                    </td>
                    <td className="py-3 px-4 font-semibold text-text">
                      {entry.tool}
                    </td>
                    <td className="py-3 px-4">
                      <span className="px-1.5 py-0.5 rounded text-[10px] uppercase bg-raised border border-border text-muted">
                        {entry.tier}
                      </span>
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`text-[11px] ${
                          entry.decision === 'execute' || entry.decision === 'approved'
                            ? 'text-success'
                            : 'text-warning'
                        }`}
                      >
                        {entry.decision}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-muted">
                      {entry.duration_ms !== undefined ? `${entry.duration_ms}ms` : '—'}
                    </td>
                    <td className="py-3 px-4">
                      {entry.error ? (
                        <span className="text-danger flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3" /> Error
                        </span>
                      ) : (
                        <span className="text-success flex items-center gap-1">
                          <CheckCircle2 className="w-3 h-3" /> OK
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
