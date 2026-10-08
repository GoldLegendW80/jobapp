import type { User } from "@/features/auth/api";

/** Up to two letters from the name (unicode aware), else the first letter of the email. */
export function initials(user: Pick<User, "name" | "email">): string {
  const words = user.name.trim().split(/\s+/).filter(Boolean);
  const letters = words
    .slice(0, 2)
    .map((word) => Array.from(word)[0] ?? "")
    .join("");
  return (letters || Array.from(user.email)[0] || "?").toLocaleUpperCase();
}
