/**
 * Typed API client for NoX Daemon REST API (Section 6.1).
 * Always includes session cookies and X-Nox-Client CSRF header for mutations.
 */

import {
  AuditEntry,
  Briefing,
  Conversation,
  Fact,
  EgressRecord,
  Episode,
  Job,
  JobPlanData,
  JobRun,
  JobRunStep,
  JobVersion,
  NotificationItem,
  SystemStatus,
  Task,
} from '../types';

const API_BASE = '/api';

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers || {});
  headers.set('Accept', 'application/json');

  // Attach session token if available
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search);
    const token =
      params.get('t') ||
      params.get('token') ||
      sessionStorage.getItem('nox_token') ||
      localStorage.getItem('nox_token');
    if (token) {
      sessionStorage.setItem('nox_token', token);
      localStorage.setItem('nox_token', token);
      if (!headers.has('Authorization')) {
        headers.set('Authorization', `Bearer ${token}`);
      }
    }
  }

  // Enforce CSRF header on state-changing requests per Section 6.1
  const method = (options.method || 'GET').toUpperCase();
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    headers.set('X-Nox-Client', 'web');
    if (options.body && !(options.body instanceof FormData)) {
      headers.set('Content-Type', 'application/json');
    }
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: 'include', // send nox_session HttpOnly cookie
  });

  if (!response.ok) {
    let errDetail = response.statusText;
    try {
      const errJson = await response.json();
      errDetail = errJson.detail || errJson.message || JSON.stringify(errJson);
    } catch {
      // ignore
    }
    throw new Error(`API ${response.status}: ${errDetail}`);
  }

  return response.json();
}

export const api = {
  // Status
  getStatus: () => request<SystemStatus>('/status'),

  // Chat & Runs
  startChat: (message: string, conversation_id?: string, allow_cloud = true, run_id?: string) =>
    request<{ run_id: string; conversation_id: string }>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id, allow_cloud, run_id }),
    }),

  cancelRun: (runId: string) =>
    request<{ status: string; run_id: string }>(`/runs/${runId}/cancel`, {
      method: 'POST',
    }),

  resolveApproval: (approvalId: number, decision: 'approve' | 'deny') =>
    request<{ ok: boolean }>(`/approvals/${approvalId}`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }),

  // Conversations
  getConversations: () => request<Conversation[]>('/conversations'),

  getConversation: (id: string) => request<Conversation>(`/conversations/${id}`),

  deleteConversation: (id: string) =>
    request<{ ok: boolean }>(`/conversations/${id}`, {
      method: 'DELETE',
    }),

  deleteAllConversations: () =>
    request<{ ok: boolean }>('/conversations', {
      method: 'DELETE',
    }),

  // Jobs
  getJobs: () => request<Job[]>('/jobs'),

  getJob: (id: string) => request<Job>(`/jobs/${id}`),

  createJob: (job: Partial<Job>) =>
    request<Job>('/jobs', {
      method: 'POST',
      body: JSON.stringify(job),
    }),

  updateJob: (id: string, updates: Partial<Job>) =>
    request<Job>(`/jobs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),

  deleteJob: (id: string) =>
    request<{ ok: boolean }>(`/jobs/${id}`, {
      method: 'DELETE',
    }),

  runJob: (id: string) =>
    request<Record<string, unknown>>(`/jobs/${id}/run`, {
      method: 'POST',
    }),

  getJobVersions: (id: string) =>
    request<JobVersion[]>(`/jobs/${id}/versions`),

  restoreJobVersion: (id: string, version: number) =>
    request<{ ok: boolean; restored_version: number }>(`/jobs/${id}/restore`, {
      method: 'POST',
      body: JSON.stringify({ version }),
    }),

  restoreJob: (id: string) =>
    request<{ ok: boolean; status: string }>(`/jobs/${id}/restore`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  getJobPlan: (id: string) =>
    request<JobPlanData>(`/jobs/${id}/plan`),

  generateJobPlan: (id: string, mode?: string) =>
    request<JobPlanData>(`/jobs/${id}/plan/generate`, {
      method: 'POST',
      body: JSON.stringify(mode ? { mode } : {}),
    }),

  approveJobPlan: (id: string, version?: number) =>
    request<{ ok: boolean; version: number }>(`/jobs/${id}/plan/approve`, {
      method: 'POST',
      body: JSON.stringify(version !== undefined ? { version } : {}),
    }),

  getJobRuns: (id: string) =>
    request<JobRun[]>(`/jobs/${id}/runs`),

  getJobRunSteps: (jobId: string, runId: string) =>
    request<JobRunStep[]>(`/jobs/${jobId}/runs/${runId}/steps`),

  // Briefings
  getBriefings: () => request<Briefing[]>('/briefings'),

  getBriefing: (id: number) => request<Briefing>(`/briefings/${id}`),

  submitBriefingFeedback: (id: number, rating: number, note?: string) =>
    request<{ ok: boolean }>(`/briefings/${id}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ rating, note }),
    }),

  deleteBriefing: (id: number | string) =>
    request<{ ok: boolean }>(`/briefings/${id}`, {
      method: 'DELETE',
    }),

  restoreBriefing: (id: number | string) =>
    request<{ ok: boolean; status: string }>(`/briefings/${id}/restore`, {
      method: 'POST',
    }),

  // Memory
  getFacts: () => request<Fact[]>('/memory/facts'),

  getThreadMemory: (convId: string) =>
    request<{
      conversation_id: string;
      summary: string | null;
      fold_index: number;
      token_count: number;
      active_goals: string[];
      entities_in_focus: string[];
      open_questions: string[];
      working_hypotheses: string[];
      active_facts: Array<{ key: string; value: string; category: string; confidence: number }>;
      messages?: Array<{ id: string; role: string; content: string; pinned: boolean; created_at: string }>;
    }>(`/memory/thread/${convId}`),

  updateThreadSummary: (convId: string, summary: string) =>
    request<{ ok: boolean; summary: string }>(`/memory/thread/${convId}/summary`, {
      method: 'PATCH',
      body: JSON.stringify({ summary }),
    }),

  pinThreadMessage: (convId: string, msgId: string) =>
    request<{ ok: boolean; pinned: boolean; message_id: string }>(
      `/memory/thread/${convId}/messages/${msgId}/pin`,
      { method: 'POST' }
    ),

  forgetThreadMessage: (convId: string, msgId: string) =>
    request<{ ok: boolean; forgotten: string }>(
      `/memory/thread/${convId}/messages/${msgId}/forget`,
      { method: 'POST' }
    ),

  resetThreadMemory: (convId: string) =>
    request<{ ok: boolean; reset: string }>(`/memory/thread/${convId}/reset`, {
      method: 'POST',
    }),

  exportThreadToVault: (convId: string) =>
    request<{ ok: boolean; path: string; uri: string }>(`/memory/thread/${convId}/export`, {
      method: 'POST',
    }),

  updateFact: (id: number, value: string) =>
    request<Fact>(`/memory/facts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ value }),
    }),

  deleteFact: (id: number) =>
    request<{ ok: boolean }>(`/memory/facts/${id}`, {
      method: 'DELETE',
    }),

  pinFact: (id: number, pinned: boolean) =>
    request<{ ok: boolean }>(`/memory/facts/${id}/pin`, {
      method: 'POST',
      body: JSON.stringify({ pinned }),
    }),

  getEpisodes: () => request<Episode[]>('/memory/episodes'),

  searchMemory: (q: string) =>
    request<{ query: string; facts: Fact[]; episodes: Episode[]; formatted: string }>(
      `/memory/search?q=${encodeURIComponent(q)}`
    ),

  // Audit
  getAuditLog: (limit = 50, offset = 0) =>
    request<AuditEntry[]>(`/audit?limit=${limit}&offset=${offset}`),

  // Settings
  getSettings: () => request<Record<string, unknown>>('/settings'),

  updateSettings: (updates: Record<string, unknown>) =>
    request<{ ok: boolean }>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),

  // Sources
  getSources: () => request<Array<Record<string, unknown>>>('/sources'),

  createSource: (src: Record<string, unknown>) =>
    request<Record<string, unknown>>('/sources', {
      method: 'POST',
      body: JSON.stringify(src),
    }),

  updateSource: (id: number, updates: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/sources/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),

  deleteSource: (id: number) =>
    request<{ ok: boolean }>(`/sources/${id}`, {
      method: 'DELETE',
    }),

  // Tasks (Task Center)
  getTasks: (lane?: string, status?: string) => {
    const params = new URLSearchParams();
    if (lane) params.set('lane', lane);
    if (status) params.set('status', status);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return request<Task[]>(`/tasks${qs}`);
  },

  getTask: (id: string) => request<Task>(`/tasks/${id}`),

  createTask: (task: { title: string; prompt: string; lane?: string; priority?: string }) =>
    request<Task>('/tasks', {
      method: 'POST',
      body: JSON.stringify(task),
    }),

  cancelTask: (id: string) =>
    request<{ ok: boolean; status: string }>(`/tasks/${id}/cancel`, {
      method: 'POST',
    }),

  deleteTask: (id: string) =>
    request<{ ok: boolean; status: string }>(`/tasks/${id}`, {
      method: 'DELETE',
    }),

  restoreTask: (id: string) =>
    request<{ ok: boolean; status: string }>(`/tasks/${id}/restore`, {
      method: 'POST',
    }),

  clearFinishedTasks: () =>
    request<{ ok: boolean; cleared: number }>('/tasks/clear-finished', {
      method: 'POST',
    }),

  // Egress Ledger
  getEgressLedger: (limit = 50) =>
    request<EgressRecord[]>(`/egress?limit=${limit}`),

  // KeyPool (Phase 3)
  getKeysStatus: () =>
    request<Array<{
      key_id: string;
      masked: string;
      status: string;
      in_flight: number;
      rpm_limit: number;
      tokens_left: number;
      ewma_latency_ms: number;
      total_requests_today: number;
      rate_limit_count_today: number;
      pinned_job_id: string | null;
      cooldown_until: number | null;
    }>>('/keys/status'),

  testKeys: () =>
    request<Array<{
      key_id: string;
      masked: string;
      status_code: number;
      latency_ms: number;
      ok: boolean;
      error?: string;
    }>>('/keys/test', { method: 'POST' }),

  getBriefingStatus: () =>
    request<{
      active: boolean;
      active_run?: {
        run_id: string;
        date: string;
        slot: string;
        rev: number;
        state: string;
        trigger: string;
        progress: number;
        topics_completed: number;
        topics_total: number;
        topics?: Array<{
          id: string;
          name: string;
          state: string;
          items: number;
        }>;
      };
      next_morning?: string;
      next_evening?: string;
      timezone?: string;
      last_runs?: {
        morning?: Record<string, unknown> | null;
        evening?: Record<string, unknown> | null;
      };
      missed_runs?: Array<{
        id: string;
        date: string;
        slot: string;
        created_at: string;
      }>;
    }>('/briefings/status'),

  runBriefing: (slot?: 'morning' | 'evening') =>
    request<{
      ok: boolean;
      run_id: string;
      date: string;
      slot: string;
      rev: number;
      active?: boolean;
      task_id?: string;
    }>('/briefings/run', {
      method: 'POST',
      body: JSON.stringify(slot ? { slot } : {}),
    }),

  cancelBriefingRun: (runId: string) =>
    request<{ ok: boolean; run_id: string }>(`/briefings/runs/${runId}/cancel`, {
      method: 'POST',
    }),

  runBriefingSlot: (slot: 'morning' | 'evening' = 'morning', force = false) =>
    request<{ ok: boolean; task_id?: string; slot: string; status?: string }>('/briefings/run', {
      method: 'POST',
      body: JSON.stringify({ slot, force }),
    }),

  submitDetailedBriefingFeedback: (
    briefingId: string,
    feedback: {
      rating: 'like' | 'dislike';
      topic_id?: string;
      item_id?: string;
      reason_text?: string;
      tags?: string[];
    }
  ) =>
    request<{ ok: boolean; feedback_id: string }>(`/briefings/${briefingId}/feedback/detailed`, {
      method: 'POST',
      body: JSON.stringify(feedback),
    }),

  getBriefingItems: (briefingId: string | number) =>
    request<Array<{
      id: string;
      briefing_id: string;
      topic_id: string;
      category: string;
      title: string;
      summary: string;
      source_name: string;
      url: string;
      created_at: string;
      kind?: string;
      organization?: string;
      country?: string;
      mode?: string;
      deadline?: string;
      score?: number;
      opportunity_status?: string;
      benefits?: {
        housing?: string;
        stipend?: string;
        travel?: string;
        abroad?: boolean;
        visa_assistance?: string;
      };
      eligibility?: {
        level?: string;
        target_audience?: string;
        open_to_international?: boolean;
      };
      score_breakdown?: Record<string, number>;
      source_evidence_snippet?: string;
      source_evidence_url?: string;
    }>>(`/briefings/${briefingId}/items`),

  updateOpportunityStatus: (canonicalUrl: string, status: string) =>
    request<{ ok: boolean; canonical_url: string; status: string }>(
      '/briefings/opportunities/status',
      {
        method: 'POST',
        body: JSON.stringify({ canonical_url: canonicalUrl, status }),
      }
    ),

  getNotesVaultInfo: () =>
    request<{
      vault_path: string;
      obsidian_uri: string;
      has_obsidian: boolean;
    }>('/notes/info'),

  openNotesVault: () =>
    request<{
      ok: boolean;
      opened: boolean;
      target: string;
      vault_path: string;
      obsidian_uri: string;
    }>('/notes/open', { method: 'POST' }),

  // Purge & Notifications
  purgeDeleted: (params: { all?: boolean; days?: number } = {}) =>
    request<{ ok: boolean; purged: Record<string, number> }>('/purge', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  getNotifications: (unreadOnly = false) =>
    request<NotificationItem[]>(`/notifications?unread_only=${unreadOnly}`),

  clearNotifications: () =>
    request<{ ok: boolean; count: number }>('/notifications/clear', {
      method: 'POST',
    }),

  markNotificationRead: (id: number) =>
    request<{ ok: boolean }>(`/notifications/${id}/read`, {
      method: 'POST',
    }),
};
