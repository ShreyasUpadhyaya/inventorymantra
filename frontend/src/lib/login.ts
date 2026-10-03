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
