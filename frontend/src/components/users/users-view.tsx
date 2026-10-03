"use client";

import { CircleAlert, Copy, KeyRound, UserPlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useSession } from "@/components/session-provider";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { ROLE_LABELS, type Role } from "@/lib/roles";
import { users, validateNewUser, type NewUserErrors, type UserRow } from "@/lib/users";

type Secret = { name: string; email: string; password: string; kind: "created" | "reset" };
type Pending = { id: string; action: "deactivate" | "reset" };

const ROLES = Object.keys(ROLE_LABELS) as Role[];
const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Something went wrong. Please try again.";

function TempPassword({ secret, onDone }: { secret: Secret; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Alert>
      <KeyRound aria-hidden />
      <AlertTitle>
        {secret.kind === "created" ? "Account created" : "Password reset"} for {secret.name}
      </AlertTitle>
      <AlertDescription className="grid gap-2">
        <p>
          Give {secret.email} this temporary password. It is shown only once, and they must choose their own
          at first sign-in.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="rounded bg-muted px-2 py-1 font-mono text-sm">{secret.password}</code>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              navigator.clipboard.writeText(secret.password).then(
                () => setCopied(true),
                () => undefined,
              )
            }
          >
            <Copy aria-hidden />
            {copied ? "Copied" : "Copy"}
          </Button>
          <Button size="sm" onClick={onDone}>
            Done, I&apos;ve passed it on
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

function AddUser({ onCreated }: { onCreated: (secret: Secret) => void }) {
  const [open, setOpen] = useState(false);
  const [errors, setErrors] = useState<NewUserErrors>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const input = {
      email: String(form.get("email") ?? "").trim(),
      full_name: String(form.get("full_name") ?? "").trim(),
      role: String(form.get("role") ?? ""),
    };
    const found = validateNewUser(input);
    setErrors(found);
    setError(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const created = await users.create({ ...input, role: input.role as Role });
      onCreated({
        name: created.full_name,
        email: created.email,
        password: created.temp_password,
        kind: "created",
      });
      setOpen(false);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div>
        <Button onClick={() => setOpen(true)}>
          <UserPlus aria-hidden />
          Add user
        </Button>
      </div>
    );
  }
  const aria = (name: keyof NewUserErrors) => ({
    "aria-invalid": errors[name] ? (true as const) : undefined,
    "aria-describedby": errors[name] ? `${name}-error` : undefined,
  });
  const fieldError = (name: keyof NewUserErrors) =>
    errors[name] && (
      <p id={`${name}-error`} className="text-sm text-destructive">
        {errors[name]}
      </p>
    );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a user</CardTitle>
        <CardDescription>
          The role decides what they can see and do, and it can&apos;t be changed later.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
          {error && (
            <Alert variant="destructive" className="sm:col-span-2">
              <CircleAlert aria-hidden />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <div className="grid gap-2">
            <Label htmlFor="full_name">Full name</Label>
            <Input id="full_name" name="full_name" autoComplete="off" autoFocus {...aria("full_name")} />
            {fieldError("full_name")}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" name="email" type="email" autoComplete="off" {...aria("email")} />
            {fieldError("email")}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="role">Role</Label>
            <select
              id="role"
              name="role"
              defaultValue=""
              className="h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              {...aria("role")}
            >
              <option value="" disabled>
                Choose a role…
              </option>
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
            {fieldError("role")}
          </div>
          <div className="flex items-end gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Creating…" : "Create account"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export function UsersView() {
  const { user: me } = useSession();
  const [rows, setRows] = useState<UserRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secret, setSecret] = useState<Secret | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    users.list().then(setRows, (err: unknown) => setError(message(err)));
  }, []);
  useEffect(load, [load]);

  async function run(row: UserRow, action: "deactivate" | "activate" | "reset") {
    setBusyId(row.id);
    setPending(null);
    setError(null);
    try {
      if (action === "reset") {
        const reset = await users.resetPassword(row.id);
        setSecret({ name: row.full_name, email: row.email, password: reset.temp_password, kind: "reset" });
      } else {
        await users.setActive(row.id, action === "activate");
      }
      load();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-xl font-semibold">Users</h1>
        <p className="text-sm text-muted-foreground">
          Accounts are created here, by the Owner. New and reset accounts get a temporary password.
        </p>
      </div>
      {secret && <TempPassword secret={secret} onDone={() => setSecret(null)} />}
      <AddUser
        onCreated={(created) => {
          setSecret(created);
          load();
        }}
      />
      {error && (
        <Alert variant="destructive">
          <CircleAlert aria-hidden />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted">
            <tr>
              {["Name", "Role", "Status", "Added", "Actions"].map((h) => (
                <th key={h} scope="col" className="px-3 py-2 text-left font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows === null && !error && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-muted-foreground">
                  Loading…
                </td>
              </tr>
            )}
            {rows?.map((row) => {
              const self = row.id === me.id;
              const ask = pending?.id === row.id ? pending.action : null;
              return (
                <tr key={row.id} className="border-t align-top">
                  <td className="px-3 py-2">
                    <div className="font-medium">
                      {row.full_name} {self && <span className="text-xs text-muted-foreground">(you)</span>}
                    </div>
                    <div className="text-xs text-muted-foreground">{row.email}</div>
                  </td>
                  <td className="px-3 py-2">{ROLE_LABELS[row.role]}</td>
                  <td className="px-3 py-2">
                    <Badge variant={row.is_active ? "secondary" : "outline"}>
                      {row.is_active ? "Active" : "Deactivated"}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {formatDate(row.created_at)}
                    {row.created_by_name && <div className="text-xs">by {row.created_by_name}</div>}
                  </td>
                  <td className="px-3 py-2">
                    {self ? null : ask ? (
                      <div className="flex flex-wrap items-center gap-1">
                        <span className="text-xs text-muted-foreground">
                          {ask === "reset"
                            ? "Sign them out and issue a new password?"
                            : "Sign them out and block access?"}
                        </span>
                        <Button size="xs" variant="destructive" onClick={() => run(row, ask)}>
                          Confirm
                        </Button>
                        <Button size="xs" variant="ghost" onClick={() => setPending(null)}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {row.is_active ? (
                          <>
                            <Button
                              size="xs"
                              variant="outline"
                              disabled={busyId === row.id}
                              onClick={() => setPending({ id: row.id, action: "reset" })}
                            >
                              Reset password
                            </Button>
                            <Button
                              size="xs"
                              variant="outline"
                              disabled={busyId === row.id}
                              onClick={() => setPending({ id: row.id, action: "deactivate" })}
                            >
                              Deactivate
                            </Button>
                          </>
                        ) : (
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={busyId === row.id}
                            onClick={() => run(row, "activate")}
                          >
                            Reactivate
                          </Button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
