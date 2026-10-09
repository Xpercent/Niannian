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
export type Notify = (message: string, error?: boolean) => void;
