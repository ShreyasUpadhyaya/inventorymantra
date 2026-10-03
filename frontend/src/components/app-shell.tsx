"use client";

import { LogOut, TrainFront } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { useSession } from "@/components/session-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HOME, ROLE_LABELS, canVisit, navFor } from "@/lib/roles";
import { cn } from "@/lib/utils";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const allowed = canVisit(user.role, pathname);

  useEffect(() => {
    if (!allowed) router.replace(HOME); // e.g. an Inventory Manager typing /users
  }, [allowed, router]);

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="border-b bg-background">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-4 px-4">
          <Link href={HOME} className="flex items-center gap-2 font-semibold">
            <TrainFront className="size-5 text-primary" aria-hidden />
            InventoryMantra
          </Link>
          <nav className="flex items-center gap-1" aria-label="Main">
            {navFor(user.role).map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
                    active && "bg-muted font-medium text-foreground",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <div className="text-sm font-medium leading-tight">{user.full_name}</div>
              <div className="text-xs text-muted-foreground">{user.email}</div>
            </div>
            <Badge variant="secondary">{ROLE_LABELS[user.role]}</Badge>
            <Button variant="ghost" size="sm" onClick={logout}>
              <LogOut aria-hidden />
              Log out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{allowed ? children : null}</main>
    </div>
  );
}
