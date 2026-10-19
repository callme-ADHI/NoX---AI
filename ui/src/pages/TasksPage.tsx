import React, { useEffect, useState } from 'react';
import {
  ListTodo,
  Plus,
  RefreshCw,
  CheckCircle2,
  Clock,
  AlertCircle,
  Ban,
  ShieldAlert,
  Play,
} from 'lucide-react';
import { Task, WebSocketEvent } from '../types';
import { api } from '../services/api';
import { wsService } from '../services/websocket';

export const TasksPage: React.FC = () => {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [filterTab, setFilterTab] = useState<'all' | 'active' | 'waiting' | 'completed'>('all');
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  // New task form state
  const [newTitle, setNewTitle] = useState('');
  const [newPrompt, setNewPrompt] = useState('');
  const [newLane, setNewLane] = useState('local');
  const [newPriority, setNewPriority] = useState('normal');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    loadTasks();
  }, []);

  // Listen to WebSocket events for real-time task updates
  useEffect(() => {
    const unsub = wsService.subscribe((event: WebSocketEvent) => {
      if (event.type.startsWith('task_')) {
        loadTasks(false);
      }
    });
    return unsub;
  }, []);

  const loadTasks = async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    try {
      const data = await api.getTasks();
      setTasks(data);
    } catch (err) {
      console.error('Failed to load tasks:', err);
    } finally {
      if (showSpinner) setLoading(false);
    }
  };

  const handleCancelTask = async (taskId: string) => {
    try {
      await api.cancelTask(taskId);
      loadTasks(false);
    } catch (err) {
      console.error('Failed to cancel task:', err);
    }
  };

  const handleApproveTask = async (approvalId: number) => {
    try {
      await api.resolveApproval(approvalId, 'approve');
      loadTasks(false);
    } catch (err) {
      console.error('Failed to approve task:', err);
    }
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim() || !newPrompt.trim()) return;

    setCreating(true);
    try {
      await api.createTask({
        title: newTitle.trim(),
        prompt: newPrompt.trim(),
        lane: newLane,
        priority: newPriority,
      });
      setNewTitle('');
      setNewPrompt('');
      setIsCreateOpen(false);
      loadTasks(false);
    } catch (err) {
      console.error('Failed to create task:', err);
    } finally {
      setCreating(false);
    }
  };

  const filteredTasks = tasks.filter((t) => {
    if (filterTab === 'active') {
      return ['queued', 'running'].includes(t.status);
    }
    if (filterTab === 'waiting') {
      return t.status === 'waiting_approval';
    }
    if (filterTab === 'completed') {
      return ['done', 'failed', 'cancelled'].includes(t.status);
    }
    return true;
  });

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'running':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-accent/20 text-accent border border-accent/30 animate-pulse">
            <Play className="w-2.5 h-2.5 fill-current" />
            Running
          </span>
        );
      case 'queued':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-surface border border-border text-muted">
            <Clock className="w-2.5 h-2.5" />
            Queued
          </span>
        );
      case 'waiting_approval':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-warning/20 text-warning border border-warning/30">
            <ShieldAlert className="w-2.5 h-2.5" />
            Waiting Approval
          </span>
        );
      case 'done':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-success/20 text-success border border-success/30">
            <CheckCircle2 className="w-2.5 h-2.5" />
            Done
          </span>
        );
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-danger/20 text-danger border border-danger/30">
            <AlertCircle className="w-2.5 h-2.5" />
            Failed
          </span>
        );
      case 'cancelled':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-surface border border-border text-muted">
            <Ban className="w-2.5 h-2.5" />
            Cancelled
          </span>
        );
      default:
        return <span className="text-muted">{status}</span>;
    }
  };

  const getPriorityBadge = (priority: string) => {
    const colors: Record<string, string> = {
      urgent: 'text-danger bg-danger/10 border-danger/30',
      high: 'text-warning bg-warning/10 border-warning/30',
      normal: 'text-text bg-raised border-border',
      low: 'text-muted bg-surface border-border',
    };
    return (
      <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono uppercase font-bold border ${colors[priority] || colors.normal}`}>
        {priority}
      </span>
    );
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto"
      tabIndex={0}
      aria-label="Task Center"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <ListTodo className="w-5 h-5 text-accent" />
            Task Center (§F5)
          </h1>
          <p className="text-xs text-muted mt-1">
            Async background execution queue, lane allocation, and approval parking.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => loadTasks(true)}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface hover:bg-raised border border-border text-xs text-text transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={() => setIsCreateOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-black hover:bg-accent/90 text-xs font-semibold shadow-sm transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Task
          </button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center gap-1 border-b border-border pb-2 mb-4 text-xs">
        {(['all', 'active', 'waiting', 'completed'] as const).map((tab) => {
          const isActive = filterTab === tab;
          const labels: Record<string, string> = {
            all: `All Tasks (${tasks.length})`,
            active: `Active (${tasks.filter((t) => ['queued', 'running'].includes(t.status)).length})`,
            waiting: `Waiting Approval (${tasks.filter((t) => t.status === 'waiting_approval').length})`,
            completed: `Completed (${tasks.filter((t) => ['done', 'failed', 'cancelled'].includes(t.status)).length})`,
          };
          return (
            <button
              key={tab}
              onClick={() => setFilterTab(tab)}
              className={`px-3 py-1.5 rounded-lg font-medium transition-colors ${
                isActive
                  ? 'bg-raised text-accent border border-accent/20'
                  : 'text-muted hover:text-text hover:bg-surface'
              }`}
            >
              {labels[tab]}
            </button>
          );
        })}
      </div>

      {/* Tasks Table */}
      <div className="flex-1 bg-surface border border-border rounded-xl overflow-hidden shadow-sm flex flex-col">
        {loading && tasks.length === 0 ? (
          <div className="p-8 text-center text-xs text-muted">Loading tasks...</div>
        ) : filteredTasks.length === 0 ? (
          <div className="p-8 text-center text-xs text-muted">No tasks found for this filter.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-border bg-raised/40 text-[11px] font-semibold text-muted uppercase tracking-wider">
                  <th className="py-2.5 px-4">Task / Title</th>
                  <th className="py-2.5 px-3">Lane</th>
                  <th className="py-2.5 px-3">Priority</th>
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-4 w-40">Progress</th>
                  <th className="py-2.5 px-3">Created</th>
                  <th className="py-2.5 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {filteredTasks.map((task) => (
                  <tr key={task.id} className="hover:bg-raised/20 transition-colors">
                    <td className="py-3 px-4 font-medium text-text">
                      <div className="font-semibold text-text">{task.title}</div>
                      <div className="text-[11px] text-muted font-mono truncate max-w-sm">{task.prompt}</div>
                    </td>
                    <td className="py-3 px-3">
                      <span className="font-mono text-[11px] text-muted capitalize">{task.lane}</span>
                    </td>
                    <td className="py-3 px-3">{getPriorityBadge(task.priority)}</td>
                    <td className="py-3 px-3">{getStatusBadge(task.status)}</td>
                    <td className="py-3 px-4">
                      <div className="w-full bg-background rounded-full h-2 border border-border overflow-hidden">
                        <div
                          className="bg-accent h-full transition-all duration-300"
                          style={{ width: `${Math.round(task.progress * 100)}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-muted mt-0.5 block text-right">
                        {Math.round(task.progress * 100)}%
                      </span>
                    </td>
                    <td className="py-3 px-3 text-[11px] font-mono text-muted">
                      {new Date(task.created_at).toLocaleTimeString()}
                    </td>
                    <td className="py-3 px-4 text-right space-x-2">
                      {task.status === 'waiting_approval' && task.approval_id && (
                        <button
                          onClick={() => handleApproveTask(task.approval_id!)}
                          className="px-2.5 py-1 rounded bg-accent text-black font-semibold text-[11px] hover:bg-accent/90"
                        >
                          Approve
                        </button>
                      )}
                      {['queued', 'running'].includes(task.status) && (
                        <button
                          onClick={() => handleCancelTask(task.id)}
                          className="px-2 py-1 rounded bg-raised hover:bg-border text-danger border border-border text-[11px]"
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create Task Modal */}
      {isCreateOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="bg-surface border border-border rounded-xl w-full max-w-md shadow-2xl p-5 text-xs animate-in zoom-in-95 duration-150">
            <h3 className="text-sm font-semibold text-text mb-3">Create Background Task</h3>
            <form onSubmit={handleCreateSubmit} className="space-y-3">
              <div>
                <label className="block text-muted mb-1 font-medium">Title</label>
                <input
                  type="text"
                  required
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="e.g. Daily security scan..."
                  className="w-full bg-background border border-border rounded-lg px-3 py-1.5 text-text focus:outline-none focus:border-accent"
                />
              </div>

              <div>
                <label className="block text-muted mb-1 font-medium">Instruction Prompt</label>
                <textarea
                  required
                  rows={3}
                  value={newPrompt}
                  onChange={(e) => setNewPrompt(e.target.value)}
                  placeholder="Task instructions to execute in the background..."
                  className="w-full bg-background border border-border rounded-lg px-3 py-1.5 text-text focus:outline-none focus:border-accent resize-none font-sans"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-muted mb-1 font-medium">Lane</label>
                  <select
                    value={newLane}
                    onChange={(e) => setNewLane(e.target.value)}
                    className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-text focus:outline-none focus:border-accent"
                  >
                    <option value="local">local (resident)</option>
                    <option value="cloud">cloud (NVIDIA)</option>
                    <option value="scheduler">scheduler</option>
                  </select>
                </div>
                <div>
                  <label className="block text-muted mb-1 font-medium">Priority</label>
                  <select
                    value={newPriority}
                    onChange={(e) => setNewPriority(e.target.value)}
                    className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-text focus:outline-none focus:border-accent"
                  >
                    <option value="low">low</option>
                    <option value="normal">normal</option>
                    <option value="high">high</option>
                    <option value="urgent">urgent</option>
                  </select>
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={() => setIsCreateOpen(false)}
                  className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating || !newTitle.trim() || !newPrompt.trim()}
                  className="px-4 py-1.5 rounded-lg bg-accent text-black font-semibold hover:bg-accent/90 disabled:opacity-50"
                >
                  {creating ? 'Creating...' : 'Create Task'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
