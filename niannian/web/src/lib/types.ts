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
export interface DailyRow {
  date: string;
  rounds: number;
  active_rounds: number;
  items: number;
  processed: number;
  created: number;
  ignored: number;
  model_requests: number;
  model_failures: number;
  wall_seconds: number;
  model_seconds: number;
}
export interface DecisionCount {
  action: string;
  source: string;
  count: number;
  last_at: string;
}
export interface IgnoredReason {
  reason: string;
  source: string;
  count: number;
  last_at: string;
}
export interface MetricsPayload {
  metrics: {
    summary: RoundSummary;
    rounds: RoundRow[];
    daily: DailyRow[];
    events: DecisionCount[];
    ignored_reasons: IgnoredReason[];
    queued: number;
    window_days: number;
    scanned: number;
    scanned_limit: number;
  };
  stats: Stats;
  model: {
    name: string;
    enabled: boolean;
    timeout_seconds: number;
    temperature: number;
    max_tokens_batch: number;
    batch_interval_seconds: number;
    settle_window_seconds: number;
    merge_window_seconds: number;
    batch_limit: number;
    batch_char_budget: number;
    round_limit: number;
    per_session_limit: number;
  };
}
export type Notify = (message: string, error?: boolean) => void;
