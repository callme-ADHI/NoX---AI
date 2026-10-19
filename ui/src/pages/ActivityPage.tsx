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
  Lock,
} from 'lucide-react';
import { AuditEntry, EgressRecord, SystemStatus } from '../types';
import { api } from '../services/api';

export const ActivityPage: React.FC = () => {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([]);
  const [egressLedger, setEgressLedger] = useState<EgressRecord[]>([]);
  const [activeTab, setActiveTab] = useState<'tools' | 'cloud'>('tools');
  const [filterQuery, setFilterQuery] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [sData, aData, eData] = await Promise.all([
        api.getStatus(),
        api.getAuditLog(100),
        api.getEgressLedger(100).catch(() => [] as EgressRecord[]),
      ]);
      setStatus(sData);
      setAuditLog(aData);
      setEgressLedger(eData);
    } catch (err) {
      console.error('Failed to load activity data:', err);
    } finally {
      setLoading(false);
    }
  };

  const filteredLogs = auditLog.filter((a) =>
    !filterQuery || a.tool.toLowerCase().includes(filterQuery.toLowerCase())
  );

  const filteredEgress = egressLedger.filter((e) =>
    !filterQuery ||
    e.model.toLowerCase().includes(filterQuery.toLowerCase()) ||
    e.reason.toLowerCase().includes(filterQuery.toLowerCase())
  );

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto"
      tabIndex={0}
      aria-label="System activity and audit log"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Activity className="w-5 h-5 text-accent" />
            System Health & Audit Activity
          </h1>
          <p className="text-xs text-muted mt-1">
            Real-time status of local models, vector storage, tool audit logs, and cloud egress ledger.
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

          {/* Card 2: Cloud Health */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-muted text-xs mb-2">
              <span className="font-semibold">Cloud Health</span>
              <Cloud className="w-4 h-4 text-sky-400" />
            </div>
            <div className="flex items-center gap-2 mb-1">
              <span
                className={`w-2 h-2 rounded-full ${
                  status.cloud_available ? 'bg-success' : 'bg-warning'
                }`}
              />
              <span className="text-sm font-bold text-text">
                {status.cloud_health?.state || (status.cloud_available ? 'Active' : 'Standby')}
              </span>
            </div>
            <div className="text-[11px] text-muted truncate">
              CB: {status.cloud_health?.circuit_breaker_state || 'CLOSED'} • 30 RPM limit
            </div>
          </div>

          {/* Card 3: Storage */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-muted text-xs mb-2">
              <span className="font-semibold">Database Size</span>
              <Database className="w-4 h-4 text-accent" />
            </div>
            <div className="text-sm font-bold text-text mb-1">
              {(status.db_size_bytes / (1024 * 1024)).toFixed(2)} MB
            </div>
            <div className="text-[11px] text-muted truncate">
              SQLite WAL + Vector Index
            </div>
          </div>

          {/* Card 4: Gate Security */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col justify-between">
            <div className="flex items-center justify-between text-muted text-xs mb-2">
              <span className="font-semibold">Gate Mode</span>
              <Shield className="w-4 h-4 text-success" />
            </div>
            <div className="text-sm font-bold text-text mb-1 capitalize">
              {status.gate_mode}
            </div>
            <div className="text-[11px] text-muted truncate">
              {status.authenticated ? 'Authenticated' : 'Lock Active'}
            </div>
          </div>
        </div>
      )}

      {/* Tab Switcher & Filter */}
      <div className="flex items-center justify-between mb-4 border-b border-border pb-3">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveTab('tools')}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors ${
              activeTab === 'tools'
                ? 'bg-raised text-accent border border-accent/20'
                : 'text-muted hover:text-text hover:bg-surface'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            Tool Execution Ledger ({auditLog.length})
          </button>
          <button
            onClick={() => setActiveTab('cloud')}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors ${
              activeTab === 'cloud'
                ? 'bg-raised text-sky-400 border border-sky-400/20'
                : 'text-muted hover:text-text hover:bg-surface'
            }`}
          >
            <Cloud className="w-3.5 h-3.5" />
            Cloud Egress Ledger ({egressLedger.length})
          </button>
        </div>

        {/* Filter input */}
        <div className="relative w-48">
          <Filter className="w-3.5 h-3.5 text-muted absolute left-2.5 top-2" />
          <input
            type="text"
            placeholder={activeTab === 'tools' ? 'Filter by tool...' : 'Filter by model/reason...'}
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            className="w-full bg-surface border border-border rounded-lg pl-8 pr-3 py-1 text-xs text-text placeholder:text-muted focus:outline-none focus:border-accent"
          />
        </div>
      </div>

      {/* Main Ledger Table */}
      <div className="flex-1 bg-surface border border-border rounded-xl overflow-hidden shadow-sm flex flex-col">
        {activeTab === 'tools' ? (
          /* Tool Audit Table */
          <div
            className="overflow-x-auto flex-1"
            tabIndex={0}
            aria-label="Audit log table"
            role="region"
          >
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
        ) : (
          /* Cloud Activity & Egress Ledger Table */
          <div
            className="overflow-x-auto flex-1 flex flex-col"
            tabIndex={0}
            aria-label="Cloud egress ledger table"
            role="region"
          >
            <div className="p-3 bg-sky-950/20 border-b border-sky-900/30 text-xs text-sky-400 flex items-center gap-2">
              <Lock className="w-3.5 h-3.5 shrink-0" />
              <span>
                <strong>Zero Prompt Storage Guaranteed:</strong> NoX records only timestamps, token counts, SHA-256 hash, and routing rationale. Zero prompt or response text is ever stored in the egress ledger (§F2, §F7).
              </span>
            </div>
            <table className="w-full text-left text-xs">
              <thead className="bg-raised/50 border-b border-border text-muted font-mono uppercase text-[10px]">
                <tr>
                  <th className="py-3 px-4">Timestamp</th>
                  <th className="py-3 px-4">Model</th>
                  <th className="py-3 px-3">Tokens Est.</th>
                  <th className="py-3 px-3">Data Class</th>
                  <th className="py-3 px-3">Decision</th>
                  <th className="py-3 px-4">Reason</th>
                  <th className="py-3 px-4 font-mono">SHA-256 Hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40 font-mono">
                {filteredEgress.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-muted font-sans text-xs">
                      No cloud egress recorded. All runs have stayed 100% local.
                    </td>
                  </tr>
                ) : (
                  filteredEgress.map((entry) => (
                    <tr key={entry.id} className="hover:bg-raised/30 transition-colors">
                      <td className="py-3 px-4 text-muted text-[11px]">
                        {new Date(entry.timestamp).toLocaleTimeString()}
                      </td>
                      <td className="py-3 px-4 font-semibold text-text">
                        {entry.model}
                      </td>
                      <td className="py-3 px-3 text-muted">{entry.prompt_tokens_est}</td>
                      <td className="py-3 px-3">
                        <span className="px-1.5 py-0.5 rounded text-[10px] uppercase bg-raised border border-border text-muted">
                          {entry.data_class}
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span
                          className={`text-[11px] font-bold ${
                            entry.decision === 'allowed'
                              ? 'text-sky-400'
                              : 'text-warning'
                          }`}
                        >
                          {entry.decision}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-text font-sans text-[11px] truncate max-w-xs">
                        {entry.reason}
                      </td>
                      <td className="py-3 px-4 text-[10px] text-muted truncate max-w-[120px]" title={entry.sha256_hash}>
                        {entry.sha256_hash ? `${entry.sha256_hash.slice(0, 12)}...` : '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
