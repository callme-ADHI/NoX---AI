import React, { useEffect, useState } from 'react';
import {
  Calendar,
  Play,
  Plus,
  X,
  ToggleLeft,
  ToggleRight,
  Trash2,
  Activity,
} from 'lucide-react';
import { Job, JobVersion, JobRun } from '../types';
import { api } from '../services/api';
import { JobEditor } from '../components/JobEditor';
import { DeleteConfirmModal } from '../components/DeleteConfirmModal';
import { UndoToast } from '../components/UndoToast';

export const JobsPage: React.FC = () => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [versions, setVersions] = useState<JobVersion[]>([]);
  const [runs, setRuns] = useState<Record<string, JobRun[]>>({});
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  // Delete & Undo state
  const [jobToDelete, setJobToDelete] = useState<Job | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [undoItem, setUndoItem] = useState<{ id: string; message: string } | null>(null);

  // New Job form state
  const [newName, setNewName] = useState('');
  const [newExpr, setNewExpr] = useState('0 9 * * *');
  const [newInstruction, setNewInstruction] = useState('');

  useEffect(() => {
    loadJobs();
  }, []);

  const loadJobs = async () => {
    try {
      const data = await api.getJobs();
      setJobs(data);

      // Load runs for active inspection
      const runsMap: Record<string, JobRun[]> = {};
      await Promise.all(
        data.map(async (j) => {
          try {
            const rList = await api.getJobRuns(j.id);
            runsMap[j.id] = rList;
          } catch {
            // ignore
          }
        })
      );
      setRuns(runsMap);
    } catch (err) {
      console.error('Failed to load jobs:', err);
    }
  };

  const handleOpenDrawer = async (job: Job) => {
    setSelectedJob(job);
    setIsDrawerOpen(true);

    try {
      const vData = await api.getJobVersions(job.id);
      setVersions(vData);
    } catch (err) {
      console.error('Failed to load job versions:', err);
    }
  };

  const handleToggleEnabled = async (job: Job, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const newEnabled = !job.enabled;
      await api.updateJob(job.id, { enabled: newEnabled });
      setJobs((prev) =>
        prev.map((j) => (j.id === job.id ? { ...j, enabled: newEnabled } : j))
      );
    } catch (err) {
      console.error('Failed to toggle job enabled:', err);
    }
  };

  const handleRunNow = async (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await api.runJob(jobId);
      loadJobs();
    } catch (err) {
      console.error('Run job error:', err);
    }
  };

  const handleDeleteJob = async () => {
    if (!jobToDelete) return;
    setDeleteLoading(true);
    try {
      await api.deleteJob(jobToDelete.id);
      const deletedName = jobToDelete.name;
      const deletedId = jobToDelete.id;
      setJobToDelete(null);
      if (selectedJob?.id === deletedId) {
        setIsDrawerOpen(false);
        setSelectedJob(null);
      }
      setUndoItem({
        id: deletedId,
        message: `Job "${deletedName}" deleted.`,
      });
      loadJobs();
    } catch (err) {
      console.error('Failed to delete job:', err);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleUndoDelete = async () => {
    if (!undoItem) return;
    try {
      await api.restoreJob(undoItem.id);
      setUndoItem(null);
      loadJobs();
    } catch (err) {
      console.error('Failed to restore job:', err);
    }
  };

  const handleRestoreVersion = async (version: number) => {
    if (!selectedJob) return;
    try {
      await api.restoreJobVersion(selectedJob.id, version);
      const reloaded = await api.getJob(selectedJob.id);
      setSelectedJob(reloaded);
      loadJobs();
      const vData = await api.getJobVersions(selectedJob.id);
      setVersions(vData);
    } catch (err) {
      console.error('Restore version error:', err);
    }
  };

  const handleCreateJob = async () => {
    if (!newName.trim() || !newExpr.trim() || !newInstruction.trim()) return;
    try {
      await api.createJob({
        name: newName.trim(),
        schedule_expr: newExpr.trim(),
        instruction: newInstruction.trim(),
        schedule_type: 'cron',
        timezone: 'Asia/Kolkata',
        enabled: true,
      });
      setIsCreateOpen(false);
      setNewName('');
      setNewInstruction('');
      loadJobs();
    } catch (err) {
      console.error('Create job error:', err);
    }
  };

  return (
    <div className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-hidden relative">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Calendar className="w-5 h-5 text-accent" />
            Scheduled Jobs & Reminders
          </h1>
          <p className="text-xs text-muted mt-1">
            Automated recurrent tasks, briefings, and natural language reminders.
          </p>
        </div>

        <button
          onClick={() => setIsCreateOpen(true)}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors shadow-sm"
        >
          <Plus className="w-4 h-4" />
          New Job
        </button>
      </div>

      {/* Jobs Table */}
      <div className="flex-1 bg-surface border border-border rounded-xl overflow-hidden shadow-sm flex flex-col">
        <div className="overflow-x-auto flex-1">
          <table className="w-full text-left text-xs">
            <thead className="bg-raised/50 border-b border-border text-muted font-mono uppercase text-[10px]">
              <tr>
                <th className="py-3 px-4">Name</th>
                <th className="py-3 px-4">Schedule</th>
                <th className="py-3 px-4">Next Run</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {jobs.map((job) => {
                const activeRun = (runs[job.id] || []).find((r) => r.status === 'running');
                return (
                  <tr
                    key={job.id}
                    onClick={() => handleOpenDrawer(job)}
                    className="hover:bg-raised/40 cursor-pointer transition-colors"
                  >
                    <td className="py-3.5 px-4">
                      <div className="font-semibold text-text">{job.name}</div>
                      <div className="text-[11px] text-muted truncate max-w-sm">
                        {job.description || job.instruction}
                      </div>
                      {activeRun && (
                        <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-semibold bg-accent/20 text-accent border border-accent/30 animate-pulse mt-1">
                          <Activity className="w-3 h-3" />
                          <span>
                            Running since {new Date(activeRun.started_at).toLocaleTimeString()} • Step {(activeRun.current_step_index ?? 0) + 1}
                          </span>
                        </div>
                      )}
                    </td>
                    <td className="py-3.5 px-4 font-mono text-muted">
                      {job.schedule_expr}
                    </td>
                    <td className="py-3.5 px-4 text-muted">
                      {job.next_run_at ? new Date(job.next_run_at).toLocaleString() : 'None'}
                    </td>
                    <td className="py-3.5 px-4">
                      <button
                        onClick={(e) => handleToggleEnabled(job, e)}
                        className="text-muted hover:text-text"
                      >
                        {job.enabled ? (
                          <span className="inline-flex items-center gap-1 text-success font-medium">
                            <ToggleRight className="w-5 h-5 text-success" /> Enabled
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-muted">
                            <ToggleLeft className="w-5 h-5" /> Disabled
                          </span>
                        )}
                      </button>
                    </td>
                    <td className="py-3.5 px-4 text-right space-x-1.5">
                      <button
                        onClick={(e) => handleRunNow(job.id, e)}
                        className="px-2.5 py-1 rounded bg-raised hover:bg-border text-text border border-border font-medium inline-flex items-center gap-1 text-[11px] transition-colors"
                      >
                        <Play className="w-3 h-3 fill-current text-accent" />
                        Run Now
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setJobToDelete(job);
                        }}
                        title="Delete job"
                        className="p-1 rounded bg-raised hover:bg-border text-danger border border-border text-[11px] transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Detail & Version History Drawer */}
      {isDrawerOpen && selectedJob && (
        <div className="fixed inset-y-0 right-0 w-full max-w-xl bg-surface border-l border-border shadow-2xl z-40 flex flex-col animate-in slide-in-from-right duration-150">
          <div className="p-4 border-b border-border flex items-center justify-between bg-raised/30">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold text-text">Job Configuration</h3>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-raised border border-border text-muted">
                v{selectedJob.version || 1}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setJobToDelete(selectedJob)}
                title="Delete this job"
                className="p-1.5 rounded text-danger hover:bg-danger/10 transition-colors"
              >
                <Trash2 className="w-4 h-4" />
              </button>
              <button
                onClick={() => setIsDrawerOpen(false)}
                className="p-1 rounded text-muted hover:text-text hover:bg-raised"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Job Editor with validation, schedule preview, and version restore */}
          <div className="flex-1 overflow-hidden">
            <JobEditor
              job={selectedJob}
              versions={versions}
              onSave={async (updated) => {
                const res = await api.updateJob(selectedJob.id, updated);
                setSelectedJob(res);
                loadJobs();
                const vData = await api.getJobVersions(selectedJob.id);
                setVersions(vData);
              }}
              onRestore={handleRestoreVersion}
            />
          </div>
        </div>
      )}

      {/* New Job Modal */}
      {isCreateOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg bg-surface border border-border rounded-xl p-6 shadow-2xl flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-border/80 pb-3">
              <h3 className="text-sm font-bold text-text">Create New Scheduled Job</h3>
              <button
                onClick={() => setIsCreateOpen(false)}
                className="text-muted hover:text-text"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <label className="block text-muted mb-1 font-semibold">Name</label>
                <input
                  type="text"
                  placeholder="e.g. system-health-check"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  className="w-full bg-background border border-border rounded-lg p-2 text-text focus:outline-none focus:border-accent"
                />
              </div>

              <div>
                <label className="block text-muted mb-1 font-semibold">Cron Expression</label>
                <input
                  type="text"
                  placeholder="0 9 * * *"
                  value={newExpr}
                  onChange={(e) => setNewExpr(e.target.value)}
                  className="w-full bg-background border border-border rounded-lg p-2 text-text font-mono focus:outline-none focus:border-accent"
                />
              </div>

              <div>
                <label className="block text-muted mb-1 font-semibold">Instruction</label>
                <textarea
                  rows={4}
                  placeholder="Tell NoX what to execute when the job triggers..."
                  value={newInstruction}
                  onChange={(e) => setNewInstruction(e.target.value)}
                  className="w-full bg-background border border-border rounded-lg p-2 text-text font-sans focus:outline-none focus:border-accent"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-border/80">
              <button
                onClick={() => setIsCreateOpen(false)}
                className="px-3 py-1.5 rounded-lg bg-raised text-muted hover:text-text border border-border text-xs"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateJob}
                className="px-4 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90"
              >
                Create Job
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      <DeleteConfirmModal
        isOpen={!!jobToDelete}
        title="Delete Scheduled Job"
        message={`Are you sure you want to delete "${jobToDelete?.name}"? Any currently active runs will be cancelled and the job moved to trash.`}
        loading={deleteLoading}
        onConfirm={handleDeleteJob}
        onCancel={() => setJobToDelete(null)}
      />

      {/* Undo Toast */}
      {undoItem && (
        <div className="fixed bottom-6 right-6 z-50">
          <UndoToast
            id={undoItem.id}
            message={undoItem.message}
            onUndo={handleUndoDelete}
            onDismiss={() => setUndoItem(null)}
          />
        </div>
      )}
    </div>
  );
};
