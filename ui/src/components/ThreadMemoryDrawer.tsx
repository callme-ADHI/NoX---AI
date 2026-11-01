import React, { useEffect, useState } from 'react';
import {
  Brain,
  X,
  Edit2,
  Check,
  FolderOpen,
  RotateCcw,
  Sparkles,
  Target,
  ShieldCheck,
  Loader2,
} from 'lucide-react';
import { api } from '../services/api';

interface ThreadMemoryData {
  conversation_id: string;
  summary: string | null;
  fold_index: number;
  token_count: number;
  active_goals: string[];
  entities_in_focus: string[];
  open_questions: string[];
  working_hypotheses: string[];
  active_facts: Array<{ key: string; value: string; category: string; confidence: number }>;
}

export interface ThreadMemoryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  convId: string | null;
}

export const ThreadMemoryDrawer: React.FC<ThreadMemoryDrawerProps> = ({
  isOpen,
  onClose,
  convId,
}) => {
  const [data, setData] = useState<ThreadMemoryData | null>(null);
  const [loading, setLoading] = useState(false);
  const [isEditingSummary, setIsEditingSummary] = useState(false);
  const [summaryInput, setSummaryInput] = useState('');
  const [savingSummary, setSavingSummary] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && convId) {
      loadMemory(convId);
    }
  }, [isOpen, convId]);

  const loadMemory = async (id: string) => {
    setLoading(true);
    try {
      const res = await api.getThreadMemory(id);
      setData(res);
      setSummaryInput(res.summary || '');
    } catch (err) {
      console.debug('Failed to load thread memory:', err);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveSummary = async () => {
    if (!convId) return;
    setSavingSummary(true);
    try {
      await api.updateThreadSummary(convId, summaryInput);
      setData((prev) => (prev ? { ...prev, summary: summaryInput } : null));
      setIsEditingSummary(false);
      flashMessage('Summary updated');
    } catch (err) {
      console.error('Failed to update summary:', err);
    } finally {
      setSavingSummary(false);
    }
  };

  const handleReset = async () => {
    if (!convId) return;
    if (!confirm("Reset this thread's memory folds and working state? Transcript messages will be preserved.")) return;
    try {
      await api.resetThreadMemory(convId);
      loadMemory(convId);
      flashMessage('Memory folds reset');
    } catch (err) {
      console.error('Failed to reset memory:', err);
    }
  };

  const handleExportVault = async () => {
    if (!convId) return;
    try {
      await api.exportThreadToVault(convId);
      flashMessage('Exported to NoX Vault');
    } catch (err) {
      console.error('Failed to export thread to vault:', err);
    }
  };

  const flashMessage = (msg: string) => {
    setActionMessage(msg);
    setTimeout(() => setActionMessage(null), 2500);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm transition-opacity">
      <div className="w-full max-w-md h-full bg-surface/95 border-l border-border flex flex-col shadow-2xl animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border bg-raised/30">
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-accent" />
            <h3 className="text-sm font-semibold text-text">What NoX Remembers Here</h3>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-muted hover:text-text hover:bg-raised transition-colors"
            title="Close memory panel"
            aria-label="Close memory panel"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
          {actionMessage && (
            <div className="p-2.5 rounded-lg bg-success/15 border border-success/30 text-success text-center text-xs font-mono">
              {actionMessage}
            </div>
          )}

          {loading ? (
            <div className="p-8 flex flex-col items-center justify-center gap-2 text-muted">
              <Loader2 className="w-5 h-5 animate-spin text-accent" />
              <span>Reading memory layers...</span>
            </div>
          ) : !convId ? (
            <div className="p-8 text-center text-muted">Select a conversation to inspect its memory.</div>
          ) : (
            <>
              {/* Rolling Summary (L3) */}
              <div className="p-3.5 rounded-xl bg-background border border-border space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-semibold text-accent uppercase tracking-wider font-mono flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5" />
                    Rolling Summary (Fold {data?.fold_index ?? 0})
                  </span>
                  {!isEditingSummary && (
                    <button
                      onClick={() => setIsEditingSummary(true)}
                      className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-accent font-medium"
                    >
                      <Edit2 className="w-3 h-3" />
                      <span>Edit</span>
                    </button>
                  )}
                </div>

                {isEditingSummary ? (
                  <div className="space-y-2 mt-2">
                    <textarea
                      value={summaryInput}
                      onChange={(e) => setSummaryInput(e.target.value)}
                      rows={4}
                      className="w-full p-2 bg-surface border border-border rounded-lg text-xs text-text focus:outline-none focus:border-accent"
                    />
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => setIsEditingSummary(false)}
                        className="px-2.5 py-1 rounded text-muted hover:text-text"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleSaveSummary}
                        disabled={savingSummary}
                        className="px-2.5 py-1 rounded bg-accent text-black font-semibold flex items-center gap-1 text-xs"
                      >
                        {savingSummary ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                        Save
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="text-muted leading-relaxed font-sans text-xs">
                    {data?.summary || 'No rolling fold has occurred yet. Context is held in active turns.'}
                  </p>
                )}
              </div>

              {/* Working Goals & Entities (L1) */}
              <div className="p-3.5 rounded-xl bg-background border border-border space-y-3">
                <span className="text-[11px] font-semibold text-text uppercase tracking-wider font-mono flex items-center gap-1.5">
                  <Target className="w-3.5 h-3.5 text-warning" />
                  Active Focus & State
                </span>

                {data?.entities_in_focus && data.entities_in_focus.length > 0 && (
                  <div>
                    <span className="text-[10px] text-muted block mb-1">Entities in Focus:</span>
                    <div className="flex flex-wrap gap-1">
                      {data.entities_in_focus.map((e, idx) => (
                        <span key={idx} className="px-2 py-0.5 rounded bg-raised border border-border text-accent font-mono text-[10px]">
                          {e}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {data?.active_goals && data.active_goals.length > 0 ? (
                  <div>
                    <span className="text-[10px] text-muted block mb-1">Current Goals:</span>
                    <ul className="space-y-1 list-disc list-inside text-muted">
                      {data.active_goals.map((g, idx) => (
                        <li key={idx} className="text-[11px] text-text">{g}</li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <div className="text-muted text-[11px]">No active multi-step goals tracked.</div>
                )}
              </div>

              {/* Pinned Facts & Profile (L1) */}
              <div className="p-3.5 rounded-xl bg-background border border-border space-y-2">
                <span className="text-[11px] font-semibold text-text uppercase tracking-wider font-mono flex items-center gap-1.5">
                  <ShieldCheck className="w-3.5 h-3.5 text-success" />
                  Active Pinned Facts
                </span>
                {data?.active_facts && data.active_facts.length > 0 ? (
                  <div className="space-y-1.5">
                    {data.active_facts.map((f, idx) => (
                      <div key={idx} className="p-2 rounded bg-surface border border-border/70 flex justify-between gap-2">
                        <span className="font-mono font-medium text-accent truncate">{f.key}</span>
                        <span className="text-text text-right truncate">{f.value}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-muted text-[11px]">No pinned facts applied to this turn.</div>
                )}
              </div>

              {/* Action Buttons */}
              <div className="pt-2 flex flex-col gap-2">
                <button
                  onClick={handleExportVault}
                  className="w-full flex items-center justify-center gap-2 p-2.5 rounded-xl border bg-raised border-border text-text hover:border-accent/40 text-xs font-medium transition-colors"
                >
                  <FolderOpen className="w-4 h-4 text-accent" />
                  Export Thread to Notes Vault
                </button>
                <button
                  onClick={handleReset}
                  className="w-full flex items-center justify-center gap-2 p-2.5 rounded-xl border bg-raised border-border text-muted hover:text-danger hover:border-danger/30 text-xs font-medium transition-colors"
                >
                  <RotateCcw className="w-4 h-4" />
                  Reset Thread Memory Folds
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
