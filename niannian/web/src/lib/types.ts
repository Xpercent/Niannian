export type TaskStatus = "pending" | "completed" | "cancelled";
export type Priority = "high" | "medium" | "low";
export interface Session {
  id: string;
  name: string;
  monitored: number | boolean;
  unread: number;
  message_count: number;
  task_count: number;
  last_message_at?: string;
}
export interface Message {
  id: number;
  sender_name: string;
  is_self: boolean;
  content: string;
  created_at: string;
  message_type: string;
  metadata?: {
    local_file?: string;
    attachment?: {
      local_file?: string;
      path?: string;
      name?: string;
      kind?: string;
    };
  };
}
export interface MessagePage {
  items: Message[];
  before_id: number | null;
  has_more: boolean;
}
export interface Task {
  id: number;
  version: number;
  title: string;
  description: string;
  context_summary: string;
  owner: string;
  priority: Priority;
  status: TaskStatus;
  session_id: string;
  session_name: string;
  due_at: string | null;
  created_at: string;
  source_message_ids: number[];
  messages?: Message[];
  events?: TaskEvent[];
}
export interface TaskEvent {
  id: number;
  action: string;
  source: string;
  created_at: string;
  detail: {
    reason?: string;
    resolution?: string;
    before?: Record<string, unknown>;
    after?: Record<string, unknown>;
  };
}
export interface Stats {
  pending: number;
  completed: number;
  cancelled: number;
  messages: number;
  queued: number;
  monitored: number;
  due_today: number;
  overdue: number;
  high_priority: number;
}
export interface Run {
  id: string;
  status: string;
  done: number;
  total: number;
  error?: string;
  session?: string;
}
export interface Overview {
  stats: Stats;
  sessions: Session[];
  wechat: {
    state: string;
    error?: string;
    account?: string;
    last_sync_at?: string;
  };
  run: Run | null;
}
export interface RoundRow {
  id: string;
  status: string;
  trigger: string | null;
  at: string;
  active: boolean;
  duration_seconds: number | null;
  model_seconds: number;
  last_model_error: string | null;
  error: string | null;
  queued_after: number | null;
  progress_done: number;
  progress_total: number;
  items: number;
  processed: number;
  created: number;
  updated: number;
  resolved: number;
  merged: number;
  ignored: number;
  skipped: number;
  cancelled: number;
  pre_filtered: number;
  llm_decided: number;
  rule_decided: number;
  batches: number;
  batched_items: number;
  model_requests: number;
  model_failures: number;
}
export interface RoundSummary {
  rounds: number;
  active_rounds: number;
  rounds_completed: number;
  rounds_failed: number;
  rounds_cancelled: number;
  rounds_running: number;
  rounds_with_model_error: number;
  rounds_with_failure: number;
  items: number;
  processed: number;
  created: number;
  updated: number;
  resolved: number;
  merged: number;
  ignored: number;
  skipped: number;
  cancelled: number;
  pre_filtered: number;
  llm_decided: number;
  rule_decided: number;
  batches: number;
  batched_items: number;
  model_requests: number;
  model_failures: number;
  wall_seconds: number;
  model_seconds: number;
  model_share: number | null;
  average_seconds: number | null;
  median_seconds: number | null;
  p95_seconds: number | null;
  slowest_seconds: number | null;
  fastest_seconds: number | null;
  seconds_per_item: number | null;
  model_failure_rate: number | null;
  local_fallback_rate: number | null;
  pre_filter_rate: number | null;
  batch_share: number | null;
  items_per_batch: number | null;
  last_round_at: string | null;
  last_active_round_at: string | null;
}
export interface IgnoredReason {
  reason: string;
  source: string;
  count: number;
  last_at: string;
}
export interface ModelBlock {
  name: string;
  enabled: boolean;
  timeout_seconds: number;
  temperature: number;
  max_tokens_batch: number;
  max_tokens_single: number;
  batch_interval_seconds: number;
  settle_window_seconds: number;
  merge_window_seconds: number;
  batch_char_budget: number;
  new_message_chars: number;
  history_chars: number;
  history_rows: number;
  task_chars: number;
}
export interface CallCounts {
  calls?: number;
  failures?: number;
  compress_calls?: number;
  batch_calls?: number;
  single_calls?: number;
  truncated?: number;
  guarded_calls?: number;
  answered?: number;
  missing?: number;
  rejected?: number;
  seconds?: number;
  first_at?: string | null;
  last_at?: string | null;
}
export interface RunRow extends RoundRow {
  calls: CallCounts;
}
export interface IssueBucket {
  type: string;
  value: string;
  count: number;
  last_at: string | null;
  last_call_id: number | null;
}
export interface GuardReasonRow {
  reason: string;
  count: number;
  last_at: string | null;
  last_call_id: number | null;
}
export interface Retention {
  keep: number;
  stored: number;
  oldest_at: string | null;
}
export interface DiagnosticsPayload {
  days: number;
  runs: RunRow[];
  summary: RoundSummary;
  ignored_reasons: IgnoredReason[];
  issues: {
    buckets: IssueBucket[];
    guard_reasons: GuardReasonRow[];
    guard_scanned: number;
  };
  retention: Retention;
  queued: number;
  model: ModelBlock;
  scanned: number;
  scanned_limit: number;
}
export interface PayloadStats {
  session_name?: string;
  is_group?: boolean;
  history_available?: number;
  history_candidates?: number;
  history_sent?: number;
  history_chars?: number;
  history_budget?: number;
  tasks_available?: number;
  tasks_sent?: number;
  tasks_chars?: number;
  tasks_budget?: number;
  profile_chars?: number;
  message_budget?: number;
  segments?: number;
  segment_chars?: number[];
  compressed_segments?: boolean[];
  system_chars?: number;
  payload_chars?: number;
  source_chars?: number;
  summary_budget?: number;
}
export interface CallRow {
  id: number;
  run_id: string | null;
  kind: string;
  session_id: string;
  session_name: string;
  message_ids: number[];
  segments: number;
  status: string;
  error: string | null;
  error_detail: string | null;
  started_at: string;
  duration_seconds: number;
  model: string;
  max_tokens: number | null;
  finish_reason: string | null;
  input_chars: number;
  output_chars: number;
  answered_items: number;
  missing_items: number;
  rejected_items: number;
  guard_changes: number;
  stats_json: PayloadStats;
}
export interface InstructionJson {
  action: string;
  title?: string | null;
  description?: string | null;
  owner?: string | null;
  priority?: string | null;
  due_at?: string | null;
  context_summary?: string | null;
  related_task_id?: number | null;
  related_task_ref?: string | null;
  resolution?: string | null;
}
export interface OutcomeItem {
  index: number;
  message_ids: number[];
  raw: unknown;
  proposed: InstructionJson[] | null;
  final: InstructionJson[] | null;
  summary?: string | null;
  note?: string | null;
  rejected?: boolean;
}
export interface ChatPayload {
  model: string;
  temperature: number;
  max_tokens: number;
  response_format?: { type: string };
  enable_thinking?: boolean;
  messages: { role: string; content: string }[];
}
export interface ModelData {
  now?: string;
  timezone?: string;
  session?: { name: string; is_group: boolean };
  user_profile?: string;
  history?: BriefMessage[];
  pending_tasks?: { id: number; title: string; priority?: string; due_at?: string | null }[];
  new_message?: BriefMessage;
  new_messages?: BriefMessage[];
  source_text?: string;
  max_summary_chars?: number;
  context_available?: boolean;
}
export interface BriefMessage {
  sender_name: string;
  content: string;
  content_is_summary?: boolean;
  is_self?: boolean;
  created_at?: string;
}
export interface CallMessage {
  id: number;
  sender_name: string;
  content: string;
  created_at: string;
  processed: boolean;
  is_self: boolean;
  summary: string | null;
}
export interface CallDetail extends CallRow {
  temperature: number | null;
  request_json: ChatPayload | null;
  response_text: string | null;
  parsed_json: unknown;
  usage_json: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } | null;
  outcome_json: { items?: OutcomeItem[]; diagnostics?: Record<string, unknown> } | null;
  segment_summary: string | null;
  messages: CallMessage[];
  events: {
    task_id: number | null;
    action: string;
    source: string;
    message_ids: number[];
    created_at: string;
    detail: Record<string, unknown>;
    title?: string | null;
    task_status?: string | null;
  }[];
  ignored: {
    id: number;
    session_id: string;
    message_ids: number[];
    source: string;
    reason: string;
    created_at: string;
  }[];
}
export interface RunSnapshot {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  trigger: string | null;
  total: number;
  done: number;
  session: string | null;
  error: string | null;
  cancel_requested?: boolean;
  result?: Record<string, number | string>;
}
export interface RunLedgerEvent {
  task_id: number | null;
  action: string;
  source: string;
  message_ids: number[];
  created_at: string;
  detail: Record<string, unknown>;
}
export interface RunDetail {
  run: RunSnapshot;
  calls: CallRow[];
  ledger: { events: RunLedgerEvent[]; ignored: CallDetail["ignored"] };
}
export interface BacklogSession {
  session_id: string;
  session_name: string;
  last_message_at: string | null;
  messages: number;
  oldest: string;
  newest: string;
}
export interface BacklogMessage {
  id: number;
  session_id: string;
  session_name: string;
  sender_name: string;
  content: string;
  created_at: string;
  calls: number;
  failures: number;
  last_call_at: string | null;
}
export interface BacklogPayload {
  sessions: BacklogSession[];
  messages: BacklogMessage[];
  touched: number;
  queued: number;
  settle_window_seconds: number;
  ai_enabled: boolean;
}
export interface CallsPayload {
  calls: CallRow[];
  has_more: boolean;
  offset: number;
}
export type Notify = (message: string, error?: boolean) => void;

