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
  JobVersion,
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
    const token = params.get('t') || params.get('token') || sessionStorage.getItem('nox_token');
    if (token) {
      sessionStorage.setItem('nox_token', token);
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
  startChat: (message: string, conversation_id?: string, allow_cloud = false) =>
    request<{ run_id: string; conversation_id: string }>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, conversation_id, allow_cloud }),
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

  // Briefings
  getBriefings: () => request<Briefing[]>('/briefings'),

  getBriefing: (id: number) => request<Briefing>(`/briefings/${id}`),

  submitBriefingFeedback: (id: number, rating: number, note?: string) =>
    request<{ ok: boolean }>(`/briefings/${id}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ rating, note }),
    }),

  // Memory
  getFacts: () => request<Fact[]>('/memory/facts'),

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

  // Egress Ledger
  getEgressLedger: (limit = 50) =>
    request<EgressRecord[]>(`/egress?limit=${limit}`),
};
