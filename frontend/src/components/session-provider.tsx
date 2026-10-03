"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { auth, type User } from "@/lib/api";

type Session = { user: User; setUser: (user: User) => void; logout: () => Promise<void> };

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside <SessionProvider>");
  return session;
}

/** Loads the signed-in user for every page behind login. If the session can't be restored,
 * the cookies are cleared (so the guard can't loop) and the user goes to /login. */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    let cancelled = false;
    auth.me().then(
      (me) => !cancelled && setUser(me),
      async () => {
        await auth.logout();
        if (!cancelled) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      },
    );
    return () => {
      cancelled = true;
    };
    // load once per mount; navigation inside the shell keeps the session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const logout = useCallback(async () => {
    await auth.logout();
    setUser(null);
    router.replace("/login");
  }, [router]);

  if (!user) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  return <SessionContext.Provider value={{ user, setUser, logout }}>{children}</SessionContext.Provider>;
}
