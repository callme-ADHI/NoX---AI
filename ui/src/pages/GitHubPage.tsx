import React, { useEffect, useState } from 'react';
import {
  GitBranch,
  RefreshCw,
  Plus,
  Trash2,
  CheckCircle2,
  XCircle,
  Clock,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  Shield,
  AlertTriangle,
  FolderGit2,
} from 'lucide-react';
import { api } from '../services/api';
import { GitHubRepo, GitHubRun } from '../types';
import { DeleteConfirmModal } from '../components/DeleteConfirmModal';

export const GitHubPage: React.FC = () => {
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [runs, setRuns] = useState<GitHubRun[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [runsLoading, setRunsLoading] = useState<boolean>(false);
  const [selectedRepo, setSelectedRepo] = useState<string>('');
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  // Add Repo Modal state
  const [isAddModalOpen, setIsAddModalOpen] = useState<boolean>(false);
  const [newRepoName, setNewRepoName] = useState<string>('');
  const [newIsPrivate, setNewIsPrivate] = useState<boolean>(false);
  const [addLoading, setAddLoading] = useState<boolean>(false);

  // Delete Repo state
  const [deleteTarget, setDeleteTarget] = useState<GitHubRepo | null>(null);
  const [deleteLoading, setDeleteLoading] = useState<boolean>(false);

  const loadRepos = async () => {
    try {
      setLoading(true);
      const data = await api.getGitHubRepos();
      setRepos(data);
      if (data.length > 0 && !selectedRepo) {
        setSelectedRepo(data[0].repo_name);
      }
    } catch (err) {
      console.error('Failed to load GitHub repos:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadRuns = async (repoName?: string) => {
    try {
      setRunsLoading(true);
      const data = await api.getGitHubRuns({ repo: repoName || undefined, limit: 30 });
      setRuns(data);
    } catch (err) {
      console.error('Failed to load GitHub runs:', err);
    } finally {
      setRunsLoading(false);
    }
  };

  useEffect(() => {
    loadRepos();
  }, []);

  useEffect(() => {
    loadRuns(selectedRepo);
  }, [selectedRepo]);

  const handleAddRepo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRepoName.trim()) return;
    try {
      setAddLoading(true);
      await api.addGitHubRepo(newRepoName.trim(), newIsPrivate, true, true);
      setNewRepoName('');
      setNewIsPrivate(false);
      setIsAddModalOpen(false);
      await loadRepos();
    } catch (err) {
      console.error('Failed to add repo:', err);
    } finally {
      setAddLoading(false);
    }
  };

  const handleDeleteRepo = async () => {
    if (!deleteTarget) return;
    try {
      setDeleteLoading(true);
      const [owner, repo] = deleteTarget.repo_name.split('/');
      await api.removeGitHubRepo(owner, repo);
      setDeleteTarget(null);
      await loadRepos();
    } catch (err) {
      console.error('Failed to remove repo:', err);
    } finally {
      setDeleteLoading(false);
    }
  };

  const toggleRunExpand = (runId: string) => {
    setExpandedRunId((prev) => (prev === runId ? null : runId));
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-5xl mx-auto w-full"
      tabIndex={0}
      aria-label="GitHub Intelligence panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <GitBranch className="w-5 h-5 text-accent" />
            GitHub & Actions Intelligence
          </h1>
          <p className="text-xs text-muted mt-1">
            Real-time GitHub Actions failure triage, CI log analysis, and guarded write operations.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsAddModalOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors shadow-sm"
          >
            <Plus className="w-3.5 h-3.5" />
            Watch Repo
          </button>
          <button
            onClick={() => {
              loadRepos();
              loadRuns(selectedRepo);
            }}
            disabled={loading || runsLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading || runsLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Watched Repos Section */}
      <div className="mb-8">
        <h2 className="text-sm font-bold text-text mb-3 flex items-center gap-2">
          <FolderGit2 className="w-4 h-4 text-accent" />
          Watched Repositories
        </h2>

        {repos.length === 0 ? (
          <div className="p-6 text-center rounded-xl bg-surface/50 border border-border text-muted text-xs">
            No repositories configured yet. Click "Watch Repo" to track Actions workflows and PRs.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {repos.map((repo) => (
              <div
                key={repo.repo_name}
                className="p-4 rounded-xl bg-surface border border-border shadow-glass flex items-center justify-between hover:border-white/20 transition-all"
              >
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-bold text-sm text-text font-mono">
                      {repo.repo_name}
                    </span>
                    {repo.is_private && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-warning/10 text-warning border border-warning/20">
                        Private
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-muted flex items-center gap-3">
                    <span>Actions: {repo.watch_actions ? 'Watching' : 'Disabled'}</span>
                    <span>PRs: {repo.watch_prs ? 'Watching' : 'Disabled'}</span>
                  </div>
                </div>

                <button
                  onClick={() => setDeleteTarget(repo)}
                  className="p-1.5 rounded-lg text-muted hover:text-danger hover:bg-danger/10 border border-transparent hover:border-danger/20 transition-colors"
                  title="Stop watching repository"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Actions Workflow Runs & Automated Triage */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-bold text-text flex items-center gap-2">
            <Clock className="w-4 h-4 text-accent" />
            Actions Runs & Automated Triage
          </h2>

          {repos.length > 0 && (
            <select
              value={selectedRepo}
              onChange={(e) => setSelectedRepo(e.target.value)}
              className="bg-surface border border-border rounded-lg px-2.5 py-1 text-xs text-text focus:outline-none focus:border-accent"
            >
              <option value="">All Watched Repos</option>
              {repos.map((r) => (
                <option key={r.repo_name} value={r.repo_name}>
                  {r.repo_name}
                </option>
              ))}
            </select>
          )}
        </div>

        {runsLoading && runs.length === 0 ? (
          <div className="p-8 text-center text-muted text-xs flex items-center justify-center gap-2">
            <RefreshCw className="w-4 h-4 animate-spin text-accent" />
            Loading GitHub Actions runs...
          </div>
        ) : runs.length === 0 ? (
          <div className="p-10 text-center rounded-xl bg-surface/50 border border-border text-muted text-xs">
            No CI workflow runs recorded yet.
          </div>
        ) : (
          <div className="space-y-3">
            {runs.map((run) => {
              const isExpanded = expandedRunId === run.id;
              const isFailed = run.conclusion === 'failure';
              const isSuccess = run.conclusion === 'success';

              return (
                <div
                  key={run.id}
                  className={`rounded-xl border transition-all ${
                    isFailed
                      ? 'bg-danger/[0.02] border-danger/30'
                      : 'bg-surface border-border hover:border-white/20'
                  }`}
                >
                  <div
                    onClick={() => toggleRunExpand(run.id)}
                    className="p-4 flex items-center justify-between cursor-pointer select-none"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <button className="text-muted hover:text-text">
                        {isExpanded ? (
                          <ChevronDown className="w-4 h-4" />
                        ) : (
                          <ChevronRight className="w-4 h-4" />
                        )}
                      </button>

                      <div className="p-1.5 rounded-lg bg-white/[0.05]">
                        {isSuccess ? (
                          <CheckCircle2 className="w-4 h-4 text-success" />
                        ) : isFailed ? (
                          <XCircle className="w-4 h-4 text-danger" />
                        ) : (
                          <Clock className="w-4 h-4 text-warning" />
                        )}
                      </div>

                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-bold text-xs text-text">
                            {run.workflow_name || 'Workflow'}
                          </span>
                          <span className="text-[11px] font-mono text-muted">
                            {run.repo_name}@{run.head_branch}
                          </span>
                          <span className="text-[10px] font-mono text-muted/70">
                            ({run.head_sha.substring(0, 7)})
                          </span>
                        </div>

                        {run.failure_step && (
                          <div className="text-[11px] text-danger font-medium mt-0.5 flex items-center gap-1">
                            <AlertTriangle className="w-3 h-3" />
                            Failed at step: {run.failure_step}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0 ml-4">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-mono uppercase font-semibold ${
                          isSuccess
                            ? 'bg-success/15 text-success border border-success/30'
                            : isFailed
                            ? 'bg-danger/15 text-danger border border-danger/30'
                            : 'bg-warning/15 text-warning border border-warning/30'
                        }`}
                      >
                        {run.conclusion || run.status}
                      </span>

                      {run.html_url && (
                        <a
                          href={run.html_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="p-1 text-muted hover:text-accent transition-colors"
                          title="Open run on GitHub"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      )}
                    </div>
                  </div>

                  {/* Expanded Automated Triage View */}
                  {isExpanded && (
                    <div className="px-5 pb-5 pt-2 border-t border-border/40 text-xs">
                      <div className="p-4 rounded-lg bg-black/40 border border-border/60">
                        <div className="text-xs font-bold text-accent mb-2 flex items-center gap-1.5">
                          <Shield className="w-3.5 h-3.5" />
                          Automated Actions Triage Summary
                        </div>
                        <div className="text-text/90 font-mono text-[11px] whitespace-pre-wrap leading-relaxed">
                          {run.triage_summary || 'No automated triage report available for this run.'}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Add Repo Modal */}
      {isAddModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in">
          <form
            onSubmit={handleAddRepo}
            className="w-full max-w-md bg-surface border border-border rounded-xl p-5 shadow-2xl flex flex-col gap-4 text-xs animate-in zoom-in-95"
          >
            <h3 className="text-sm font-bold text-text">Watch GitHub Repository</h3>
            <div>
              <label className="text-[11px] text-muted block mb-1">
                Repository Name (owner/repo):
              </label>
              <input
                type="text"
                placeholder="e.g. callme-ADHI/NoX---AI"
                value={newRepoName}
                onChange={(e) => setNewRepoName(e.target.value)}
                className="w-full bg-raised border border-border rounded-lg px-3 py-2 text-xs text-text focus:outline-none focus:border-accent"
                autoFocus
              />
            </div>

            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={newIsPrivate}
                onChange={(e) => setNewIsPrivate(e.target.checked)}
                className="rounded border-border bg-surface text-accent focus:ring-0"
              />
              <span className="text-[11px] text-text">
                Private Repository (local-only analysis strictly enforced)
              </span>
            </label>

            <div className="flex items-center justify-end gap-2 pt-3 border-t border-border">
              <button
                type="button"
                onClick={() => setIsAddModalOpen(false)}
                className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={addLoading || !newRepoName.trim()}
                className="px-3.5 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors disabled:opacity-50"
              >
                {addLoading ? 'Adding...' : 'Watch Repository'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      <DeleteConfirmModal
        isOpen={!!deleteTarget}
        title={`Stop watching ${deleteTarget?.repo_name}?`}
        message="This will remove the repository from background Actions triage and PR summaries."
        confirmLabel="Remove"
        isDangerous={true}
        loading={deleteLoading}
        onConfirm={handleDeleteRepo}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
};
