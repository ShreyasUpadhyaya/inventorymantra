"use client";

import { CircleAlert, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useSession } from "@/components/session-provider";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { VizView } from "@/components/viz/viz-view";
import { ApiError } from "@/lib/api";
import { dashboard, sectionViz, type Dashboard, type Section } from "@/lib/dashboard";
import { formatDate, formatKpi } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/roles";
import { cn } from "@/lib/utils";

const SAFE_ERROR = "The dashboard could not be loaded. Please try again.";

function SectionCard({ section }: { section: Section }) {
  const wide = section.viz === "table";
  return (
    <Card className={cn(wide && "lg:col-span-2")}>
      <CardHeader>
        <CardTitle>{section.title}</CardTitle>
      </CardHeader>
      <CardContent>
        {section.rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{section.empty_message}</p>
        ) : (
          <VizView viz={sectionViz(section)} columns={section.columns} rows={section.rows} />
        )}
      </CardContent>
    </Card>
  );
}

function Loading() {
  return (
    <div className="grid gap-4" aria-busy="true" aria-label="Loading dashboard">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-xl bg-muted" />
    </div>
  );
}

export function DashboardView() {
  const { user } = useSession();
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(() => {
    dashboard
      .get()
      .then(setData, (err: unknown) => setError(err instanceof ApiError ? err.message : SAFE_ERROR))
      .finally(() => setLoading(false));
  }, []);

  useEffect(fetchData, [fetchData]); // first load; state is only set once the request answers

  function refresh() {
    setLoading(true);
    setError(null);
    fetchData();
  }

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Hello, {user.full_name.split(" ")[0]}</h1>
          <p className="text-sm text-muted-foreground">
            {ROLE_LABELS[user.role]} dashboard
            {data && <> · figures as of {formatDate(data.today)}</>}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className={cn(loading && "animate-spin")} aria-hidden />
          Refresh
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <CircleAlert aria-hidden />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {loading && !data && <Loading />}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
            {data.kpis.map((kpi) => (
              <Card key={kpi.id} size="sm">
                <CardContent>
                  <div className="text-xs text-muted-foreground">{kpi.label}</div>
                  <div className="text-2xl font-semibold tabular-nums">{formatKpi(kpi.value, kpi.format)}</div>
                </CardContent>
              </Card>
            ))}
          </div>
          <div className={cn("grid gap-4 lg:grid-flow-dense lg:grid-cols-2", loading && "opacity-60")}>
            {data.sections.map((section) => (
              <SectionCard key={section.id} section={section} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
