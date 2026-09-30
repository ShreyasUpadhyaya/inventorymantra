"use client";

import { useEffect, useState } from "react";

type Health = { status: string; llm_mode: string; today: string };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setHealth)
      .catch(() => setError(true));
  }, []);

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight">InventoryMantra</h1>
      <p className="text-zinc-600 dark:text-zinc-400">
        Bilingual, role-aware inventory and procurement assistant. Scaffold build (Phase 1).
      </p>
      <section className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <h2 className="mb-2 font-medium">API status</h2>
        {error && <p className="text-red-600">API unreachable at {API_BASE}. Is `make api` running?</p>}
        {!error && !health && <p>Checking…</p>}
        {health && (
          <ul className="space-y-1 text-sm">
            <li>Status: <strong>{health.status}</strong></li>
            <li>LLM mode: <strong>{health.llm_mode}</strong></li>
            <li>Business date: <strong>{health.today}</strong></li>
          </ul>
        )}
      </section>
    </main>
  );
}
