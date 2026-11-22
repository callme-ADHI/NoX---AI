/**
 * Data types matching NoX backend models (Section 6.1, 8, 9).
 */

export interface CloudHealthInfo {
  state: 'ACTIVE' | 'NO_KEY' | 'INVALID_KEY' | 'RATE_LIMITED' | 'DEGRADED' | 'OFFLINE' | string;
  failures: number;
  successes: number;
  last_error?: string | null;
  circuit_breaker_state: 'CLOSED' | 'OPEN' | 'HALF_OPEN' | string;
  cooldown_remaining_seconds: number;
  average_latency_ms: number;
  total_calls: number;
}

export interface SystemStatus {
  status: string;
  authenticated: boolean;
  gate_mode: string;
  ollama_reachable: boolean;
  loaded_models: string[];
  cloud_available: boolean;
  cloud_key_configured: boolean;
  cloud_enabled?: boolean;
  active_runs_count: number;
  db_size_bytes: number;
  vector_backend: string;
  cloud_health?: CloudHealthInfo;
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
  lane?: string;
  latency_ms?: number;
  trace?: Record<string, unknown>;
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
  sources?: Array<string | { url: string; title?: string }>;
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
  visibility?: 'local_only' | 'cloud_allowed' | string;
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

export interface Task {
  id: string;
  title: string;
  prompt: string;
  lane: 'local' | 'cloud' | 'scheduler' | string;
  priority: 'low' | 'normal' | 'high' | 'urgent' | string;
  status: 'queued' | 'running' | 'paused' | 'waiting_approval' | 'done' | 'failed' | 'cancelled' | string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  result_json?: string | null;
  progress: number;
  approval_id?: number | null;
  cancellation_token?: string | null;
  run_id?: string | null;
}

export interface EgressRecord {
  id: number;
  timestamp: string;
  provider: string;
  model: string;
  prompt_tokens_est: number;
  data_class: string;
  decision: string;
  reason: string;
  sha256_hash: string;
  created_at: string;
}

export interface JobStep {
  kind: string;
  folder?: string | null;
  params: Record<string, unknown>;
  timeout_seconds: number;
  retry_policy?: {
    max_retries: number;
    backoff_seconds: number;
  };
}

export interface FolderSummary {
  folder: string;
  file_count: number;
  total_size_bytes: number;
  status: string;
  error?: string | null;
}

export interface JobPlanData {
  version: number;
  plan_json: {
    steps: JobStep[];
    folders?: string[];
  };
  source: 'cloud' | 'template' | string;
  approved_at?: string | null;
  approved_by?: string | null;
  created_at: string;
  folder_summaries?: FolderSummary[];
}

export interface JobRunStep {
  id: number;
  run_id: string;
  step_index: number;
  kind: string;
  folder?: string | null;
  status: 'pending' | 'running' | 'success' | 'failed' | 'skipped' | string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  exit_code?: number | null;
  stdout_tail?: string | null;
  stderr_tail?: string | null;
  error?: string | null;
  retry_count: number;
}

export interface JobRun {
  id: string;
  job_id: string;
  status: 'running' | 'success' | 'failed' | 'aborted' | string;
  started_at: string;
  finished_at?: string | null;
  error?: string | null;
  plan_version?: number | null;
  current_step_index?: number | null;
  heartbeat_at?: string | null;
  due_slot?: string | null;
}

export interface NotificationItem {
  id: number;
  job_id?: string | null;
  job_run_id?: string | null;
  title: string;
  message: string;
  level: 'info' | 'success' | 'warning' | 'error' | string;
  read: boolean | number;
  created_at: string;
}

export interface ConnectorInfo {
  name: string;
  type: string;
  status: string;
  healthy: boolean;
  last_sync_at?: string | null;
  error?: string | null;
  item_count: number;
}

export interface MailItem {
  id: string;
  connector_id: string;
  thread_id: string;
  sender: string;
  subject: string;
  date: string;
  category: string;
  is_phishing: boolean;
  snippet: string;
  created_at: string;
}

export interface GitHubRepo {
  repo_name: string;
  is_private: boolean;
  watch_actions: boolean;
  watch_prs: boolean;
  last_checked_at?: string | null;
  created_at: string;
}

export interface GitHubRun {
  id: string;
  repo_name: string;
  workflow_name: string;
  head_branch: string;
  head_sha: string;
  status: string;
  conclusion: string;
  html_url: string;
  failure_step?: string | null;
  triage_summary?: string | null;
  run_started_at: string;
}

export interface AgentInfo {
  id: string;
  name: string;
  mode: 'on_demand' | 'scheduled' | 'continuous' | string;
  cron_expression?: string;
  enabled: boolean;
  budget_tokens: number;
  budget_cost_usd: number;
  status: string;
  prompt_template: string;
  last_run_at?: string;
  last_error?: string;
}

export interface PresetInfo {
  name: string;
  description: string;
  active_agents: string[];
  active_connectors: string[];
  schedules?: Array<{ slot: string; cron: string }>;
  is_active: boolean;
}

export interface ScopedApproval {
  id: string;
  action: string;
  scope: string;
  payload: Record<string, unknown>;
  created_at: string;
  expires_at: string;
  status: 'pending' | 'approved' | 'denied' | 'consumed' | string;
}

export interface AuditChainEvent {
  id: string;
  sequence_num: number;
  prev_hash: string;
  current_hash: string;
  actor: string;
  action: string;
  target: string;
  decision: string;
  summary: string;
  created_at: string;
}

export interface SkillInfo {
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  allow_scripts: boolean;
  scripts?: string[];
}

export interface FullHealthDiagnostics {
  status: 'healthy' | 'degraded' | string;
  panic_engaged: boolean;
  database: {
    wal_mode: boolean;
    path: string;
    ok: boolean;
  };
  cloud: {
    active_keys: number;
    total_keys: number;
  };
  audit: {
    verified: boolean;
    total_events: number;
    message: string;
  };
  tools: {
    gh_installed: boolean;
    git_installed: boolean;
  };
}


