import React, { useState, useEffect } from 'react';
import {
  RotateCcw,
  AlertTriangle,
  Check,
  Clock,
  History,
  ListOrdered,
  Sparkles,
  Folder,
  ShieldCheck,
  RefreshCw,
} from 'lucide-react';
import { Job, JobVersion, JobPlanData } from '../types';
import { describeCron } from '../utils/cron';
import { api } from '../services/api';

export interface JobEditorProps {
  job: Job;
  versions?: JobVersion[];
  onSave: (updated: { name: string; description: string; schedule_expr: string; instruction: string }) => Promise<void>;
  onRestore?: (version: number) => Promise<void>;
}

export const JobEditor: React.FC<JobEditorProps> = ({
  job,
  versions = [],
  onSave,
  onRestore,
}) => {
  const [name, setName] = useState(job.name);
  const [description, setDescription] = useState(job.description || '');
  const [scheduleExpr, setScheduleExpr] = useState(job.schedule_expr);
  const [instruction, setInstruction] = useState(job.instruction);
  const [activeTab, setActiveTab] = useState<'details' | 'plan' | 'versions'>('details');
  const [saving, setSaving] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);

  // Plan state
  const [planData, setPlanData] = useState<JobPlanData | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [generatingPlan, setGeneratingPlan] = useState(false);
  const [approvingPlan, setApprovingPlan] = useState(false);

  useEffect(() => {
    setName(job.name);
    setDescription(job.description || '');
    setScheduleExpr(job.schedule_expr);
    setInstruction(job.instruction);
    if (activeTab === 'plan') {
      loadPlan();
    }
  }, [job, activeTab]);

  const loadPlan = async () => {
    setPlanLoading(true);
    setPlanError(null);
    try {
      const data = await api.getJobPlan(job.id);
      setPlanData(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('404')) {
        setPlanData(null);
      } else {
        setPlanError(msg);
      }
    } finally {
      setPlanLoading(false);
    }
  };

  const handleGeneratePlan = async () => {
    setGeneratingPlan(true);
    setPlanError(null);
    try {
      const generated = await api.generateJobPlan(job.id);
      setPlanData(generated);
    } catch (err: unknown) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setGeneratingPlan(false);
    }
  };

  const handleApprovePlan = async () => {
    if (!planData) return;
    setApprovingPlan(true);
    setPlanError(null);
    try {
      await api.approveJobPlan(job.id, planData.version);
      await loadPlan();
    } catch (err: unknown) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setApprovingPlan(false);
    }
  };

  const cronCheck = describeCron(scheduleExpr);
  const hasValidationErrors = !name.trim() || !instruction.trim() || !cronCheck.isValid;

  const handleSave = async () => {
    if (hasValidationErrors || saving) return;
    setSaving(true);
    setSavedSuccess(false);
    try {
      await onSave({
        name: name.trim(),
        description: description.trim(),
        schedule_expr: scheduleExpr.trim(),
        instruction: instruction.trim(),
      });
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-surface text-text text-xs">
      {/* Tabs */}
      <div className="flex border-b border-border/80 px-4 text-xs font-medium">
        <button
          onClick={() => setActiveTab('details')}
          className={`py-2.5 px-3 border-b-2 transition-colors ${
            activeTab === 'details'
              ? 'border-accent text-accent'
              : 'border-transparent text-muted hover:text-text'
          }`}
        >
          Details & Instruction
        </button>
        <button
          onClick={() => setActiveTab('plan')}
          className={`py-2.5 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === 'plan'
              ? 'border-accent text-accent'
              : 'border-transparent text-muted hover:text-text'
          }`}
        >
          <ListOrdered className="w-3.5 h-3.5" />
          Execution Plan
        </button>
        <button
          onClick={() => setActiveTab('versions')}
          className={`py-2.5 px-3 border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === 'versions'
              ? 'border-accent text-accent'
              : 'border-transparent text-muted hover:text-text'
          }`}
        >
          <History className="w-3.5 h-3.5" />
          Version History ({versions.length})
        </button>
      </div>

      {activeTab === 'details' && (
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Job Name */}
          <div>
            <label className="block text-muted mb-1 font-semibold">Job Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-background border border-border rounded-lg p-2 text-text focus:outline-none focus:border-accent"
              placeholder="e.g. daily-briefing"
            />
            {!name.trim() && (
              <span className="text-[11px] text-danger mt-1 block">Job name is required</span>
            )}
          </div>

          {/* Description */}
          <div>
            <label className="block text-muted mb-1 font-semibold">Description</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full bg-background border border-border rounded-lg p-2 text-text focus:outline-none focus:border-accent"
            />
          </div>

          {/* Schedule */}
          <div>
            <label className="block text-muted mb-1 font-semibold">Cron Schedule</label>
            <input
              type="text"
              value={scheduleExpr}
              onChange={(e) => setScheduleExpr(e.target.value)}
              className={`w-full bg-background border rounded-lg p-2 text-text font-mono focus:outline-none ${
                cronCheck.isValid ? 'border-border focus:border-accent' : 'border-danger focus:border-danger'
              }`}
              placeholder="e.g. 0 9 * * *"
            />

            {/* Validation & Preview */}
            <div className="mt-1.5">
              {cronCheck.isValid ? (
                <div className="flex items-center gap-1.5 text-[11px] text-accent font-medium">
                  <Clock className="w-3.5 h-3.5" />
                  <span>Preview: {cronCheck.preview}</span>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-[11px] text-danger font-medium">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <span>Error: {cronCheck.error}</span>
                </div>
              )}
            </div>
          </div>

          {/* Instruction */}
          <div>
            <label className="block text-muted mb-1 font-semibold">Agent Instruction</label>
            <textarea
              rows={8}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              className="w-full bg-background border border-border rounded-lg p-2 text-text font-sans focus:outline-none focus:border-accent leading-relaxed"
            />
            {!instruction.trim() && (
              <span className="text-[11px] text-danger mt-1 block">Instruction is required</span>
            )}
          </div>

          <div className="pt-2">
            <button
              disabled={hasValidationErrors || saving}
              onClick={handleSave}
              className={`w-full py-2 font-semibold rounded-lg transition-colors shadow-sm flex items-center justify-center gap-2 ${
                hasValidationErrors || saving
                  ? 'bg-raised text-muted border border-border cursor-not-allowed opacity-50'
                  : 'bg-accent text-black hover:bg-accent/90'
              }`}
            >
              {savedSuccess ? (
                <>
                  <Check className="w-4 h-4 text-success" />
                  <span>Saved Successfully!</span>
                </>
              ) : saving ? (
                <span>Saving...</span>
              ) : (
                <span>Save Changes (New Snapshot)</span>
              )}
            </button>
          </div>
        </div>
      )}

      {activeTab === 'plan' && (
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {planLoading ? (
            <div className="p-8 text-center text-xs text-muted flex flex-col items-center gap-2">
              <RefreshCw className="w-5 h-5 animate-spin text-accent" />
              <span>Loading execution plan...</span>
            </div>
          ) : planError ? (
            <div className="p-4 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs flex items-center justify-between">
              <span>{planError}</span>
              <button
                onClick={loadPlan}
                className="px-2 py-1 rounded bg-raised text-text border border-border"
              >
                Retry
              </button>
            </div>
          ) : !planData ? (
            <div className="p-8 text-center text-xs text-muted space-y-3">
              <ListOrdered className="w-8 h-8 text-muted/60 mx-auto" />
              <p>No execution plan found for this job.</p>
              <p className="text-[11px] text-muted max-w-xs mx-auto">
                Generate a validated, step-by-step plan before executing scheduled runs.
              </p>
              <button
                disabled={generatingPlan}
                onClick={handleGeneratePlan}
                className="px-4 py-2 rounded-lg bg-accent text-black font-semibold inline-flex items-center gap-2 hover:bg-accent/90"
              >
                <Sparkles className="w-4 h-4" />
                {generatingPlan ? 'Generating...' : 'Generate Plan with AI'}
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Header Status & Action Bar */}
              <div className="p-3 rounded-lg bg-background border border-border flex items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-text font-mono">Plan v{planData.version}</span>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
                      planData.approved_at
                        ? 'bg-success/20 text-success border-success/30'
                        : 'bg-warning/20 text-warning border-warning/30'
                    }`}>
                      {planData.approved_at ? 'Approved' : 'Draft (Approval Required)'}
                    </span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-raised border border-border text-muted font-mono uppercase">
                      {planData.source}
                    </span>
                  </div>
                  {planData.approved_at ? (
                    <div className="text-[10px] text-muted mt-1">
                      Approved by <span className="font-medium text-text">{planData.approved_by || 'owner'}</span> on {new Date(planData.approved_at).toLocaleString()}
                    </div>
                  ) : (
                    <div className="text-[10px] text-warning mt-1">
                      This plan requires explicit approval before scheduled runs can proceed.
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  <button
                    disabled={generatingPlan || approvingPlan}
                    onClick={handleGeneratePlan}
                    title="Regenerate plan"
                    className="px-2.5 py-1.5 rounded bg-raised hover:bg-border text-text border border-border text-[11px] font-medium flex items-center gap-1.5"
                  >
                    <RefreshCw className={`w-3 h-3 ${generatingPlan ? 'animate-spin' : ''}`} />
                    Re-Plan
                  </button>
                  {!planData.approved_at && (
                    <button
                      disabled={approvingPlan || generatingPlan}
                      onClick={handleApprovePlan}
                      className="px-3 py-1.5 rounded bg-accent text-black font-semibold text-[11px] hover:bg-accent/90 flex items-center gap-1.5 shadow-sm"
                    >
                      <ShieldCheck className="w-3.5 h-3.5" />
                      {approvingPlan ? 'Approving...' : 'Approve Plan'}
                    </button>
                  )}
                </div>
              </div>

              {/* Folder Summaries */}
              {planData.folder_summaries && planData.folder_summaries.length > 0 && (
                <div>
                  <h4 className="text-[11px] font-bold text-muted uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    <Folder className="w-3.5 h-3.5 text-accent" />
                    Target Folders ({planData.folder_summaries.length})
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    {planData.folder_summaries.map((f, idx) => (
                      <div
                        key={idx}
                        className="p-2.5 rounded-lg bg-background border border-border flex flex-col justify-between"
                      >
                        <div className="font-semibold text-text truncate font-mono text-[11px]" title={f.folder}>
                          {f.folder}
                        </div>
                        <div className="flex items-center justify-between mt-2 text-[10px] text-muted">
                          <span>{f.file_count} files</span>
                          <span>{(f.total_size_bytes / 1024).toFixed(1)} KB</span>
                          <span className={`px-1.5 py-0.5 rounded font-mono ${
                            f.status === 'clean' ? 'text-success bg-success/10' :
                            f.status === 'modified' ? 'text-warning bg-warning/10' :
                            'text-muted bg-raised'
                          }`}>
                            {f.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Execution Steps */}
              <div>
                <h4 className="text-[11px] font-bold text-muted uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <ListOrdered className="w-3.5 h-3.5 text-accent" />
                  Planned Steps ({planData.plan_json?.steps?.length || 0})
                </h4>
                <div className="space-y-1.5">
                  {planData.plan_json?.steps?.map((step, idx) => (
                    <div
                      key={idx}
                      className="p-2.5 rounded-lg bg-background border border-border/80 flex items-start gap-3 text-xs"
                    >
                      <span className="w-5 h-5 rounded-full bg-raised border border-border text-[10px] font-mono font-bold flex items-center justify-center shrink-0 text-muted">
                        {idx + 1}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-semibold text-accent text-[11px]">
                            {step.kind}
                          </span>
                          {step.folder && (
                            <span className="text-[10px] text-muted font-mono truncate max-w-xs">
                              ({step.folder})
                            </span>
                          )}
                        </div>
                        {step.params && Object.keys(step.params).length > 0 && (
                          <div className="text-[10px] font-mono text-muted/80 mt-1 truncate">
                            {JSON.stringify(step.params)}
                          </div>
                        )}
                      </div>
                      <div className="text-right shrink-0 text-[10px] font-mono text-muted">
                        <div>{step.timeout_seconds}s timeout</div>
                        {step.retry_policy && (
                          <div className="text-[9px] text-muted/70">
                            {step.retry_policy.max_retries} retries
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'versions' && (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {versions.length === 0 ? (
            <p className="text-muted text-center py-6">No historical versions available.</p>
          ) : (
            versions.map((ver) => (
              <div
                key={ver.id}
                className="p-3 rounded-lg bg-background border border-border/70 flex flex-col gap-2 text-xs"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-accent font-mono">v{ver.version}</span>
                    <span className="text-[11px] text-muted">
                      by {ver.changed_by} {ver.changed_by === 'agent' && '(NoX)'}
                    </span>
                  </div>
                  {onRestore && ver.version !== job.version && (
                    <button
                      onClick={() => onRestore(ver.version)}
                      className="px-2 py-1 rounded bg-raised hover:bg-border border border-border text-[11px] font-medium text-text flex items-center gap-1 transition-colors"
                    >
                      <RotateCcw className="w-3 h-3 text-accent" />
                      Restore
                    </button>
                  )}
                </div>
                {ver.change_note && (
                  <div className="text-[11px] text-muted italic">
                    "{ver.change_note}"
                  </div>
                )}
                <div className="text-[10px] text-muted font-mono">
                  {new Date(ver.created_at).toLocaleString()}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};
