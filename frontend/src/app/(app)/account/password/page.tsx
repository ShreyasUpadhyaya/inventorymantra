"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useSession } from "@/components/session-provider";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, auth } from "@/lib/api";
import { type PasswordErrors, validatePasswordChange } from "@/lib/login";
import { HOME } from "@/lib/roles";

const FIELDS = [
  { name: "current", label: "Current password", autoComplete: "current-password" },
  { name: "next", label: "New password (at least 10 characters)", autoComplete: "new-password" },
  { name: "confirm", label: "Repeat new password", autoComplete: "new-password" },
] as const;

export default function ChangePasswordPage() {
  const { user, setUser } = useSession();
  const router = useRouter();
  const forced = user.must_change_password;
  const [errors, setErrors] = useState<PasswordErrors>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const value = (name: string) => String(form.get(name) ?? "");
    const found = validatePasswordChange(user.email, value("current"), value("next"), value("confirm"));
    setErrors(found);
    setError(null);
    setDone(false);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const updated = await auth.changePassword(value("current"), value("next"));
      setUser(updated);
      formElement.reset();
      if (forced) router.replace(HOME);
      else setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mx-auto w-full max-w-md">
      <CardHeader>
        <CardTitle>{forced ? "Choose your own password" : "Change password"}</CardTitle>
        <CardDescription>
          {forced
            ? "You signed in with a temporary password from the Owner. Choose a new one to continue."
            : "You'll stay signed in here; other devices are signed out."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4" noValidate>
          {error && (
            <Alert variant="destructive">
              <CircleAlert aria-hidden />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {done && (
            <Alert>
              <CircleCheck aria-hidden />
              <AlertDescription>Password changed. Other devices have been signed out.</AlertDescription>
            </Alert>
          )}
          {FIELDS.map((field) => (
            <div key={field.name} className="grid gap-2">
              <Label htmlFor={field.name}>{field.label}</Label>
              <Input
                id={field.name}
                name={field.name}
                type="password"
                autoComplete={field.autoComplete}
                aria-invalid={errors[field.name] ? true : undefined}
                aria-describedby={errors[field.name] ? `${field.name}-error` : undefined}
              />
              {errors[field.name] && (
                <p id={`${field.name}-error`} className="text-sm text-destructive">
                  {errors[field.name]}
                </p>
              )}
            </div>
          ))}
          <Button type="submit" size="lg" disabled={busy}>
            {busy ? "Saving…" : "Save new password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
