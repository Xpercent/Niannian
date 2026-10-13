import type { BriefMessage, ModelData } from "./types";

function messageFromXml(node: Element): BriefMessage {
  return {
    sender_name: node.getAttribute("sender") || "",
    content: node.querySelector(":scope > content")?.textContent || "",
    is_self: node.getAttribute("self") === "true",
    created_at: node.getAttribute("time") || undefined,
    content_is_summary: node.getAttribute("summary") === "true",
  };
}

// 旧留档的 user 正文是单行 JSON，2026-10-13 起是 XML；两种都要能读出来。
export function parseModelContext(raw: string): ModelData | null {
  const text = raw.trim();
  if (!text) return null;
  if (!text.startsWith("<")) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        return null;
      const data = parsed as ModelData;
      const message = (value: unknown): value is BriefMessage => {
        if (!value || typeof value !== "object" || Array.isArray(value))
          return false;
        const row = value as BriefMessage;
        return (
          typeof row.sender_name === "string" &&
          typeof row.content === "string" &&
          typeof row.is_self === "boolean"
        );
      };
      for (const rows of [data.new_messages, data.history]) {
        if (
          rows !== undefined &&
          (!Array.isArray(rows) || !rows.every(message))
        )
          return null;
      }
      if (data.new_message !== undefined && !message(data.new_message))
        return null;
      if (
        data.pending_tasks !== undefined &&
        (!Array.isArray(data.pending_tasks) ||
          !data.pending_tasks.every(
            (task) =>
              task &&
              typeof task.id === "number" &&
              typeof task.title === "string",
          ))
      )
        return null;
      if (
        data.user_profile !== undefined &&
        typeof data.user_profile !== "string"
      )
        return null;
      if (
        data.source_text !== undefined &&
        typeof data.source_text !== "string"
      )
        return null;
      return data;
    } catch {
      return null;
    }
  }
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) return null;
  const root = doc.documentElement;
  if (root.nodeName === "compression") {
    return {
      source_text: root.querySelector(":scope > source")?.textContent || "",
      max_summary_chars: Number(root.getAttribute("max_summary_chars")) || 0,
      context_available: root.getAttribute("context_available") === "true",
    };
  }
  if (root.nodeName !== "extraction") return null;
  const session = root.querySelector(":scope > context > session");
  return {
    now:
      root.querySelector(":scope > context > reference_time")?.textContent ||
      "",
    timezone:
      root.querySelector(":scope > context > timezone")?.textContent || "",
    session: {
      name: session?.getAttribute("name") || "",
      is_group: session?.getAttribute("group") === "true",
    },
    user_profile:
      root.querySelector(":scope > user_profile")?.textContent || "",
    history: [...root.querySelectorAll(":scope > history > message")].map(
      messageFromXml,
    ),
    pending_tasks: [
      ...root.querySelectorAll(":scope > pending_tasks > task"),
    ].map((task) => ({
      id: Number(task.getAttribute("id")),
      title: task.textContent || "",
      priority: task.getAttribute("priority") || undefined,
      due_at: task.getAttribute("due_at") || null,
    })),
    new_messages: [
      ...root.querySelectorAll(":scope > current_messages > message"),
    ].map(messageFromXml),
  };
}
