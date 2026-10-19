import React, { useState } from 'react';
import { X, ShieldCheck, AlertTriangle, Zap, CheckCircle2, ChevronDown, ChevronRight, Terminal } from 'lucide-react';

export interface TraceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  messageId?: string;
  lane?: string;
  modelName?: string;
  isCloud?: boolean;
  latencyMs?: number;
  trace?: Record<string, unknown>;
}

export const TraceDrawer: React.FC<TraceDrawerProps> = ({
  isOpen,
  onClose,
  lane = 'front_desk',
  modelName,
  isCloud = false,
  latencyMs,
  trace,
}) => {
  const [showRawJson, setShowRawJson] = useState(false);

  if (!isOpen) return null;

  const intentData = (trace?.intent as Record<string, unknown>) || {};
  const intentName = (intentData.intent as string) || 'reply_now';
  const replyMode = (intentData.reply_mode as string) || 'instant';
  const toolsCalled = (trace?.tools as Array<Record<string, unknown>>) || [];
  const taintFlags = (trace?.taint_flags as number) || 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm transition-opacity">
      <div className="w-full max-w-md h-full bg-surface border-l border-border flex flex-col shadow-2xl animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border bg-raised/30">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-accent" />
            <h3 className="text-sm font-semibold text-text">Execution & Routing Trace</h3>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-muted hover:text-text hover:bg-raised transition-colors"
            title="Close drawer (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
          {/* Routing Summary */}
          <div className="p-3 rounded-xl bg-background border border-border space-y-2">
            <h4 className="text-[11px] font-semibold text-muted uppercase tracking-wider">
              Routing Resolution
            </h4>
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              <div>
                <span className="text-muted block">Execution Lane:</span>
                <span className="font-mono font-medium text-text capitalize">{lane.replace('_', ' ')}</span>
              </div>
              <div>
                <span className="text-muted block">Provider / Model:</span>
                <span className="font-mono font-medium text-text truncate block">
                  {modelName || (isCloud ? 'Nemotron (Cloud)' : 'Qwen 2.5 (Local)')}
                </span>
              </div>
              <div>
                <span className="text-muted block">Privacy Tier:</span>
                <span className="font-medium text-accent">
                  {isCloud ? 'Cloud Eligible (Public)' : 'Local Isolation (Zero Egress)'}
                </span>
              </div>
              <div>
                <span className="text-muted block">Round-trip Latency:</span>
                <span className="font-mono font-medium text-text">{latencyMs ?? 0} ms</span>
              </div>
            </div>
          </div>

          {/* Intent Analysis (§F4) */}
          <div className="p-3 rounded-xl bg-background border border-border space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-[11px] font-semibold text-muted uppercase tracking-wider">
                Reflex Intent Analysis
              </h4>
              <span className="text-[10px] font-mono text-accent bg-accent/10 px-1.5 py-0.5 rounded">
                &lt;5ms Zero-LLM
              </span>
            </div>
            <div className="space-y-1 text-[11px]">
              <div className="flex justify-between">
                <span className="text-muted">Detected Intent:</span>
                <span className="font-mono font-bold text-text bg-raised px-1.5 py-0.5 rounded border border-border">
                  {intentName}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Reply Mode:</span>
                <span className="font-mono text-text capitalize">{replyMode}</span>
              </div>
            </div>
          </div>

          {/* Taint & Privacy State */}
          <div className="p-3 rounded-xl bg-background border border-border space-y-2">
            <h4 className="text-[11px] font-semibold text-muted uppercase tracking-wider">
              Taint & Integrity Flags
            </h4>
            <div className="flex items-center gap-2">
              {taintFlags === 0 ? (
                <div className="flex items-center gap-1.5 text-success text-[11px]">
                  <ShieldCheck className="w-4 h-4" />
                  <span>Clean — no untrusted inputs or leaked secrets</span>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-warning text-[11px]">
                  <AlertTriangle className="w-4 h-4" />
                  <span>Tainted flags active ({taintFlags})</span>
                </div>
              )}
            </div>
          </div>

          {/* Tools Called */}
          <div className="p-3 rounded-xl bg-background border border-border space-y-2">
            <h4 className="text-[11px] font-semibold text-muted uppercase tracking-wider flex items-center justify-between">
              <span>Tools Executed</span>
              <span className="text-text font-mono font-normal">({toolsCalled.length})</span>
            </h4>
            {toolsCalled.length === 0 ? (
              <div className="text-muted text-[11px] py-1">No tools called for this turn (direct synthesis).</div>
            ) : (
              <div className="space-y-2">
                {toolsCalled.map((tool, idx) => (
                  <div key={idx} className="p-2 rounded-lg bg-surface border border-border/70 space-y-1">
                    <div className="flex items-center justify-between font-mono font-semibold text-[11px] text-text">
                      <span className="flex items-center gap-1">
                        <Terminal className="w-3 h-3 text-accent" />
                        {String(tool.name || 'tool')}
                      </span>
                      <CheckCircle2 className="w-3 h-3 text-success" />
                    </div>
                    {Boolean(tool.arguments) && (
                      <pre className="p-1.5 rounded bg-background border border-border font-mono text-[10px] overflow-x-auto text-muted">
                        {JSON.stringify(tool.arguments, null, 2)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Raw JSON collapse */}
          <div className="border border-border rounded-xl p-3 bg-background">
            <button
              onClick={() => setShowRawJson(!showRawJson)}
              className="w-full flex items-center justify-between text-[11px] font-medium text-muted hover:text-text"
            >
              <span>View Raw Event Trace</span>
              {showRawJson ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            </button>
            {showRawJson && (
              <pre className="mt-2 p-2 bg-surface border border-border rounded-lg font-mono text-[10px] overflow-x-auto text-muted max-h-48 leading-relaxed">
                {JSON.stringify({ lane, modelName, isCloud, latencyMs, ...trace }, null, 2)}
              </pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
