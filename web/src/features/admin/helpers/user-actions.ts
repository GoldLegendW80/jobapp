import type { AdminUser, UserChange } from "../api/admin-api";

export type UserAction = "promote" | "demote" | "disable" | "enable";

/** An action the admin picked and has yet to confirm; `self` when the account is their own. */
export interface PendingChange {
  user: AdminUser;
  action: UserAction;
  self: boolean;
}

/** What each action sends to the api. */
export const USER_CHANGE: Record<UserAction, UserChange> = {
  promote: { role: "admin" },
  demote: { role: "member" },
  disable: { is_active: false },
  enable: { is_active: true },
};

export const LAST_ADMIN_NOTE =
  "The last active admin can be neither disabled nor made a member. Make someone else an admin first.";

export const displayName = (user: AdminUser) => user.name || user.email;

/** The words of the confirmation of an action: its question, its consequence, its buttons. */
export function confirmationWords({ user, action, self }: PendingChange) {
  const name = displayName(user);
  switch (action) {
    case "promote":
      return {
        title: `Make ${name} an admin?`,
        body: `${name} can then invite people, send reset links and manage every account, yours included.`,
        confirm: "Make admin",
        busy: "Saving",
        done: `${name} is now an admin.`,
      };
    case "demote":
      return self
        ? {
            title: "Give up your admin role?",
            body: "You lose the admin area at once and stay signed in as a member. Another admin can make you an admin again.",
            confirm: "Make me a member",
            busy: "Saving",
            done: "You are now a member.",
          }
        : {
            title: `Make ${name} a member?`,
            body: `${name} loses the admin area at once and stays signed in as a member.`,
            confirm: "Make member",
            busy: "Saving",
            done: `${name} is now a member.`,
          };
    case "disable":
      return {
        title: `Disable ${name}?`,
        body: `${name} is signed out of every browser at once and cannot sign in until you enable the account again. Their data stays.`,
        confirm: "Disable",
        busy: "Disabling",
        done: `${name} is disabled and signed out everywhere.`,
      };
    case "enable":
      return {
        title: `Enable ${name}?`,
        body: `${name} can sign in again with their password. Browsers that were signed in before stay signed out.`,
        confirm: "Enable",
        busy: "Enabling",
        done: `${name} can sign in again.`,
      };
  }
}
