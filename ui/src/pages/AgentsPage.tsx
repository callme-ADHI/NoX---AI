import React, { useEffect, useState } from 'react';
import {
  Bot,
  RefreshCw,
  Play,
  ToggleLeft,
  ToggleRight,
  Clock,
  Coins,
  CheckCircle2,
  Sliders,
  X,
  Zap,
} from 'lucide-react';
import { api } from '../services/api';
import { AgentInfo, PresetInfo } from '../types';

export const AgentsPage: React.FC = () => {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [presets, setPresets] = useState<PresetInfo[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [applyingPreset, setApplyingPreset] = useState<string | null>(null);

  // Run Agent Modal
  const [runTarget, setRunTarget] = useState<AgentInfo | null>(null);
  const [runPrompt, setRunPrompt] = useState<string>('');
  const [running, setRunning] = useState<boolean>(false);
  const [runResult, setRunResult] = useState<Record<string, unknown> | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      const [agentsData, presetsData] = await Promise.all([
        api.getAgents(),
        api.getPresets(),
      ]);
      setAgents(agentsData);
      setPresets(presetsData);
    } catch (err) {
      console.error('Failed to load agents or presets:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleToggleAgent = async (name: string) => {
    try {
      await api.toggleAgent(name);
      setAgents((prev) =>
        prev.map((a) => (a.name === name ? { ...a, enabled: !a.enabled } : a))
      );
    } catch (err) {
      console.error('Failed to toggle agent:', err);
    }
  };

  const handleApplyPreset = async (presetName: string) => {
    try {
      setApplyingPreset(presetName);
      await api.applyPreset(presetName);
      await loadData();
    } catch (err) {
      console.error('Failed to apply preset:', err);
    } finally {
      setApplyingPreset(null);
    }
  };

  const handleExecuteRun = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!runTarget) return;
    try {
      setRunning(true);
      setRunResult(null);
      const res = await api.runAgent(runTarget.name, runPrompt);
      setRunResult(res);
      await loadData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setRunResult({ error: msg });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-5xl mx-auto w-full"
      tabIndex={0}
      aria-label="Agents & Presets panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Bot className="w-5 h-5 text-accent" />
            Autonomous Agents & Presets
          </h1>
          <p className="text-xs text-muted mt-1">
            Multi-mode runner (on-demand, scheduled, continuous) with token and cost budgets and kill switch safety.
          </p>
        </div>

        <button
          onClick={loadData}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Operational Presets Banner */}
      <div className="mb-8 p-5 rounded-xl bg-surface border border-border shadow-glass">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Sliders className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-bold text-text">Operational Presets</h2>
          </div>
          <span className="text-[11px] text-muted">
            Instant full-subsystem profile configuration
          </span>
        </div>

        <div className="flex flex-wrap gap-2">
          {presets.map((preset) => {
            const isActive = preset.is_active;
            const isApplying = applyingPreset === preset.name;

            return (
              <button
                key={preset.name}
                onClick={() => handleApplyPreset(preset.name)}
                disabled={isActive || isApplying}
                className={`px-3 py-2 rounded-xl border text-xs font-medium transition-all flex items-center gap-2 ${
                  isActive
                    ? 'bg-accent/15 border-accent text-accent shadow-sm'
                    : 'bg-white/[0.03] border-white/10 text-muted hover:text-text hover:bg-white/[0.06]'
                }`}
                title={preset.description}
              >
                <span className="font-mono">{preset.name}</span>
                {isActive && <CheckCircle2 className="w-3.5 h-3.5 text-accent" />}
                {isApplying && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
              </button>
            );
          })}
        </div>
      </div>

      {/* Agents Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {agents.map((agent) => {
          const isEnabled = agent.enabled;

          return (
            <div
              key={agent.id}
              className={`p-5 rounded-xl border shadow-glass flex flex-col justify-between transition-all ${
                isEnabled
                  ? 'bg-surface border-border hover:border-white/20'
                  : 'bg-surface/40 border-border/50 opacity-75'
              }`}
            >
              <div>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-white/[0.05] border border-white/10 text-accent">
                      <Bot className="w-5 h-5" />
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-text font-mono">
                        {agent.name}
                      </h3>
                      <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-white/[0.05] border border-white/10 text-muted inline-block mt-0.5">
                        Mode: {agent.mode}
                      </span>
                    </div>
                  </div>

                  <button
                    onClick={() => handleToggleAgent(agent.name)}
                    className="p-1 text-muted hover:text-text transition-colors"
                    title={isEnabled ? 'Disable agent' : 'Enable agent'}
                  >
                    {isEnabled ? (
                      <ToggleRight className="w-6 h-6 text-accent" />
                    ) : (
                      <ToggleLeft className="w-6 h-6 text-muted" />
                    )}
                  </button>
                </div>

                <div className="space-y-1.5 py-2 text-xs border-t border-border/40 text-muted">
                  <div className="flex justify-between items-center">
                    <span className="flex items-center gap-1">
                      <Coins className="w-3.5 h-3.5 text-warning" />
                      Budget:
                    </span>
                    <span className="font-mono text-text">
                      {agent.budget_tokens.toLocaleString()} tokens (${agent.budget_cost_usd.toFixed(2)})
                    </span>
                  </div>

                  {agent.cron_expression && (
                    <div className="flex justify-between items-center">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3.5 h-3.5 text-accent" />
                        Schedule:
                      </span>
                      <span className="font-mono text-text">{agent.cron_expression}</span>
                    </div>
                  )}

                  <div className="flex justify-between items-center">
                    <span>Status:</span>
                    <span className="font-mono text-text capitalize">{agent.status}</span>
                  </div>

                  {agent.last_run_at && (
                    <div className="flex justify-between items-center">
                      <span>Last Run:</span>
                      <span className="font-mono text-text">
                        {new Date(agent.last_run_at).toLocaleString()}
                      </span>
                    </div>
                  )}

                  {agent.last_error && (
                    <div className="text-danger text-[11px] mt-1 break-all">
                      {agent.last_error}
                    </div>
                  )}
                </div>
              </div>

              {/* Action */}
              <div className="pt-3 mt-3 border-t border-border/40 flex items-center justify-end">
                <button
                  onClick={() => {
                    setRunTarget(agent);
                    setRunPrompt('');
                    setRunResult(null);
                  }}
                  className="px-3 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors flex items-center gap-1.5 shadow-sm"
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  Run On-Demand
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Run Agent Modal */}
      {runTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in">
          <form
            onSubmit={handleExecuteRun}
            className="w-full max-w-xl bg-surface border border-border rounded-xl p-5 shadow-2xl flex flex-col gap-4 text-xs animate-in zoom-in-95"
          >
            <div className="flex items-center justify-between border-b border-border/70 pb-3">
              <div className="flex items-center gap-2">
                <Zap className="w-4 h-4 text-accent" />
                <h3 className="text-sm font-bold text-text">
                  Execute Agent: <span className="font-mono text-accent">{runTarget.name}</span>
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setRunTarget(null)}
                className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div>
              <label className="text-[11px] text-muted block mb-1">
                Input Prompt / Task Instructions:
              </label>
              <textarea
                rows={3}
                placeholder="Enter prompt or leave empty for default trigger..."
                value={runPrompt}
                onChange={(e) => setRunPrompt(e.target.value)}
                className="w-full bg-raised border border-border rounded-lg px-3 py-2 text-xs text-text focus:outline-none focus:border-accent font-sans"
              />
            </div>

            {runResult && (
              <div className="p-3 rounded-lg bg-black/40 border border-border/60 text-xs text-text/90 font-mono max-h-48 overflow-y-auto whitespace-pre-wrap">
                {JSON.stringify(runResult, null, 2)}
              </div>
            )}

            <div className="flex items-center justify-between pt-3 border-t border-border">
              <span className="text-[11px] text-muted">
                Executes in sandboxed subagent runner with active budget checks.
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setRunTarget(null)}
                  className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
                >
                  Close
                </button>
                <button
                  type="submit"
                  disabled={running}
                  className="px-3.5 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                >
                  <Play className={`w-3.5 h-3.5 ${running ? 'animate-spin' : 'fill-current'}`} />
                  {running ? 'Running...' : 'Execute'}
                </button>
              </div>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
