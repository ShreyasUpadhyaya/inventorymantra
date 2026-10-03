// Optimistic route guard (Next.js 16 "proxy", formerly middleware). It only checks that an
// access cookie exists; the API checks it properly on every call (CLAUDE.md §5).
// No cookie → /login?next=…; the login page first tries a silent refresh, so an expired
// 15-minute access token doesn't force a new login.

import { NextResponse, type NextRequest } from "next/server";

import { HOME, isProtected } from "@/lib/roles";

const ACCESS_COOKIE = "im_access"; // backend/app/auth/tokens.py

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (pathname === "/") {
    return NextResponse.redirect(new URL(HOME, request.url));
  }
  if (isProtected(pathname) && !request.cookies.has(ACCESS_COOKIE)) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
