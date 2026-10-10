import { ModelAccessCard } from "@/features/agents/model-access-card";
import { useMe } from "@/features/auth/auth-context";
import { useTitle } from "@/lib/use-title";

import { NameCard } from "./name-card";
import { PasswordCard } from "./password-card";

/** /account: your name, your password, and the model credential your agents use. */
export function AccountPage() {
  useTitle("Account");
  const { data: user } = useMe();
  // Inside RequireAuth there is always a user; the check keeps the types honest.
  if (!user) return null;
  return (
    <div className="mx-auto flex max-w-xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Account</h1>
        <p className="text-muted-foreground">
          Your name, the password you sign in with, and the model access your
          agents run on.
        </p>
      </header>
      <NameCard key={user.name} user={user} />
      <PasswordCard user={user} />
      <ModelAccessCard />
    </div>
  );
}
