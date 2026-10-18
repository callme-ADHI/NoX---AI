/**
 * Data types matching NoX backend models (Section 6.1, 8, 9).
 */

export interface SystemStatus {
  status: string;
  authenticated: boolean;
  gate_mode: string;
  ollama_reachable: boolean;
  loaded_models: string[];
  cloud_available: boolean;
  cloud_key_configured: boolean;
  active_runs_count: number;
  db_size_bytes: number;
  vector_backend: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages?: Message[];
}

export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at?: string;
  model_name?: string;
  is_cloud?: boolean;
}

export interface ToolCallItem {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  tier?: 'read' | 'write' | 'dangerous';
  result_summary?: string;
  duration_ms?: number;
  ok?: boolean;
  expanded?: boolean;
}

export interface ApprovalRequest {
  id: number;
  run_id?: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
  tier: 'write' | 'dangerous';
  untrusted_warning?: boolean;
  expires_at?: string;
  status: 'pending' | 'approved' | 'denied' | 'consumed' | 'expired';
}

export interface SubtaskNode {
  id: string;
  title: string;
  objective: string;
  kind: 'research' | 'fetch' | 'compute' | 'action';
  depends_on: string[];
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped';
  summary?: string;
  sources?: string[];
  duration_ms?: number;
}

export interface TaskPlan {
  run_id: string;
  goal: string;
  nodes: SubtaskNode[];
  overall_progress: number;
  started_at: number;
  finished?: boolean;
}

export interface Job {
  id: string;
  name: string;
  description: string;
  schedule_type: string;
  schedule_expr: string;
  timezone: string;
  instruction: string;
  config: Record<string, unknown>;
  enabled: boolean | number;
  version?: number;
  next_run_at?: string;
  last_status?: string;
}

export interface JobVersion {
  id: number;
  job_id: string;
  version: number;
  snapshot_json: string;
  changed_by: string;
  change_note?: string;
  created_at: string;
}

export interface Briefing {
  id: number;
  job_run_id: string;
  title: string;
  markdown: string;
  sources_json?: string;
  feedback?: number | null;
  feedback_note?: string;
  created_at: string;
}

export interface Fact {
  id: number;
  key: string;
  value: string;
  category: string;
  confidence: number;
  pinned: boolean | number;
  needs_review?: boolean | number;
  provenance?: string;
  created_at: string;
  updated_at: string;
}

export interface Episode {
  id: number;
  ts: string;
  kind: string;
  topic?: string;
  summary: string;
}

export interface AuditEntry {
  id: number;
  ts: string;
  tool: string;
  tier: string;
  args_hash?: string;
  decision: string;
  duration_ms?: number;
  run_id?: string;
  error?: string;
}

export interface WebSocketEvent {
  id: string;
  ts: string;
  run_id?: string;
  type: string;
  data: Record<string, unknown>;
}
