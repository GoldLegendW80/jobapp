/**
 * The password rules of the api (app/identity/security.py), checked here first so most mistakes
 * show at once. The api stays the judge: it also refuses common passwords, and its message is
 * shown as it comes. Messages match the api's word for word.
 */
export const PASSWORD_MIN = 10;
export const PASSWORD_MAX = 128;
export const NAME_MAX = 100;

export const PASSWORD_HINT = `Use ${PASSWORD_MIN} to ${PASSWORD_MAX} characters. It cannot be your email address or a common password.`;

/** Length in characters as the api counts them: code points after NFKC normalisation. */
export function passwordLength(password: string): number {
  return Array.from(password.normalize("NFKC")).length;
}

/** The first broken rule, or undefined when the password passes the client side checks. */
export function passwordProblem(
  password: string,
  email: string,
): string | undefined {
  if (password === "") return "Choose a password.";
  const length = passwordLength(password);
  if (length < PASSWORD_MIN)
    return `Use at least ${PASSWORD_MIN} characters for the password.`;
  if (length > PASSWORD_MAX)
    return `Use at most ${PASSWORD_MAX} characters for the password.`;
  const fold = (value: string) => value.normalize("NFKC").trim().toLowerCase();
  if (fold(password) === fold(email))
    return "The password cannot be your email address.";
  return undefined;
}

/** The display name as the api stores it: whitespace runs collapsed, trimmed. */
export function cleanName(name: string): string {
  return name.normalize("NFC").split(/\s+/).filter(Boolean).join(" ");
}

export function nameProblem(name: string): string | undefined {
  const clean = cleanName(name);
  if (clean === "") return "Enter your name.";
  if (Array.from(clean).length > NAME_MAX)
    return `Use at most ${NAME_MAX} characters for the name.`;
  return undefined;
}
