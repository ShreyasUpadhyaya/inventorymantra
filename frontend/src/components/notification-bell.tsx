"use client";

import { Bell } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ago, notifications, type Inbox } from "@/lib/notifications";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

/** Bell with the unread count; the panel lists your latest notifications. */
export function NotificationBell() {
  const [inbox, setInbox] = useState<Inbox>({ unread: 0, items: [] });
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  const refresh = useCallback(() => {
    notifications.list().then(setInbox, () => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  async function markRead(id: string) {
    await notifications.read(id).catch(() => undefined);
    refresh();
  }

  async function markAll() {
    await notifications.readAll().catch(() => undefined);
    refresh();
  }

  return (
    <div className="relative" ref={panel}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={`Notifications${inbox.unread ? `, ${inbox.unread} unread` : ""}`}
        aria-expanded={open}
      >
        <Bell className="size-4" aria-hidden />
        {inbox.unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-destructive px-1 text-center text-[10px] font-semibold leading-4 text-white">
            {inbox.unread > 9 ? "9+" : inbox.unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-xl border bg-background shadow-lg">
          <div className="flex items-center justify-between border-b px-4 py-2">
            <span className="text-sm font-medium">Notifications</span>
            {inbox.unread > 0 && (
              <Button variant="link" size="xs" className="px-0" onClick={markAll}>
                Mark all as read
              </Button>
            )}
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {inbox.items.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted-foreground">Nothing new.</li>
            )}
            {inbox.items.map((n) => (
              <li key={n.id} className="border-b last:border-b-0">
                <button
                  type="button"
                  onClick={() => !n.read && markRead(n.id)}
                  className={cn("grid w-full gap-0.5 px-4 py-2.5 text-left hover:bg-muted", !n.read && "bg-primary/5")}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    {!n.read && <span className="size-2 rounded-full bg-primary" aria-label="unread" />}
                    {n.title}
                  </span>
                  {n.body && <span className="text-sm text-muted-foreground">{n.body}</span>}
                  <span className="text-xs text-muted-foreground">{ago(n.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
