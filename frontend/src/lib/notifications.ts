// Notifications API (backend/app/api/routes/notifications.py). Only the signed-in user's own.

import { apiFetch, apiJson } from "./api";

export type AppNotification = {
  id: string;
  type: string;
  title: string;
  body: string | null;
  created_at: string;
  read: boolean;
};

export type Inbox = { unread: number; items: AppNotification[] };

export const notifications = {
  list: () => apiJson<Inbox>("/notifications"),
  read: (id: string) => apiFetch(`/notifications/${id}/read`, { method: "POST" }),
  readAll: () => apiFetch("/notifications/read-all", { method: "POST" }),
};

/** "5 min ago", "3 h ago", else the date. */
export function ago(iso: string, now: Date = new Date()): string {
  const minutes = Math.round((now.getTime() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`;
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
