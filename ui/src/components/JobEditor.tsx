import React, { useState, useEffect } from 'react';
import { RotateCcw, AlertTriangle, Check, Clock, History } from 'lucide-react';
import { Job, JobVersion } from '../types';
import { describeCron } from '../utils/cron';

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
  const [activeTab, setActiveTab] = useState<'details' | 'versions'>('details');
  const [saving, setSaving] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);

  useEffect(() => {
    setName(job.name);
    setDescription(job.description || '');
    setScheduleExpr(job.schedule_expr);
    setInstruction(job.instruction);
  }, [job]);

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
