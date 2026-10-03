// Owner's user management API (backend/app/api/routes/users.py).

import { apiJson } from "./api";
import type { Role } from "./roles";

export type UserRow = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  created_by_name: string | null;
};

export type NewUser = { email: string; full_name: string; role: Role };

export const users = {
  list: () => apiJson<UserRow[]>("/users"),
  create: (body: NewUser) =>
    apiJson<UserRow & { temp_password: string }>("/users", { method: "POST", body: JSON.stringify(body) }),
  setActive: (id: string, active: boolean) =>
    apiJson<UserRow>(`/users/${id}/${active ? "activate" : "deactivate"}`, { method: "POST" }),
  resetPassword: (id: string) =>
    apiJson<{ user: UserRow; temp_password: string }>(`/users/${id}/reset-password`, { method: "POST" }),
};

export type NewUserErrors = { email?: string; full_name?: string; role?: string };

export function validateNewUser(input: { email: string; full_name: string; role: string }): NewUserErrors {
  const errors: NewUserErrors = {};
  if (!input.full_name.trim()) errors.full_name = "Enter the person's name.";
  const email = input.email.trim();
  if (!email) errors.email = "Enter an email address.";
  else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.email = "Enter a valid email address.";
  if (!["inventory_manager", "procurement_manager", "owner"].includes(input.role)) {
    errors.role = "Choose a role.";
  }
  return errors;
}
