// Checks run before the login request, so empty fields get a clear message instead of a 422.

export type LoginErrors = { email?: string; password?: string };

export function validateLogin(email: string, password: string): LoginErrors {
  const errors: LoginErrors = {};
  const trimmed = email.trim();
  if (!trimmed) errors.email = "Enter your email.";
  else if (!/^[^\s@]+@[^\s@]+$/.test(trimmed)) errors.email = "Enter a valid email address.";
  if (!password) errors.password = "Enter your password.";
  return errors;
}

export type PasswordErrors = { current?: string; next?: string; confirm?: string };

/** Same rules as the API (backend/app/auth/service.py password_problem), checked early. */
export function validatePasswordChange(
  email: string,
  current: string,
  next: string,
  confirm: string,
): PasswordErrors {
  const errors: PasswordErrors = {};
  if (!current) errors.current = "Enter your current password.";
  if (next.length < 10) errors.next = "Use at least 10 characters.";
  else if (next === current) errors.next = "Choose a password different from the current one.";
  else if (next.trim().toLowerCase() === email.toLowerCase())
    errors.next = "Don't use your email as your password.";
  if (!errors.next && confirm !== next) errors.confirm = "The two new passwords don't match.";
  return errors;
}
