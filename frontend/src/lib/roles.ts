// Roles and which pages each may open. This is UX only: the API is the authority (CLAUDE.md §5).

export type Role = "inventory_manager" | "procurement_manager" | "owner";

export const ROLE_LABELS: Record<Role, string> = {
  inventory_manager: "Inventory Manager",
  procurement_manager: "Procurement Manager",
  owner: "Owner",
};

export type NavItem = { href: string; label: string; roles: readonly Role[] };

const ALL: readonly Role[] = ["inventory_manager", "procurement_manager", "owner"];

export const NAV: readonly NavItem[] = [
  { href: "/dashboard", label: "Dashboard", roles: ALL },
  { href: "/chat", label: "Ask", roles: ALL },
  { href: "/users", label: "Users", roles: ["owner"] },
];

export const HOME = "/dashboard";

/** Pages behind login. Anything else (login, Next assets, /api) is public. */
export function isProtected(pathname: string): boolean {
  return NAV.some((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
}

export function canVisit(role: Role, pathname: string): boolean {
  const item = NAV.find((n) => pathname === n.href || pathname.startsWith(`${n.href}/`));
  return item === undefined || item.roles.includes(role);
}

export function navFor(role: Role): NavItem[] {
  return NAV.filter((item) => item.roles.includes(role));
}

/** Where to go after login. Only same-site paths, so ?next= can't send users elsewhere. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return HOME;
  }
  return next.startsWith("/login") ? HOME : next;
}
