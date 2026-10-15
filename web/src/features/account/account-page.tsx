import { ModelAccessCard } from "@/features/agents/model-access-card";
import { useMe } from "@/features/auth/auth-context";
import { BrowserCard } from "@/features/browser/browser-card";
import { GmailCard } from "@/features/mail/gmail-card";
import { useTitle } from "@/lib/use-title";

import { NameCard } from "./name-card";
import { PasswordCard } from "./password-card";

/** /account: your name, your password, the model credential your agents use, and your Gmail. */
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
          Your name, the password you sign in with, the model access your agents
          run on, and your Gmail connection.
        </p>
      </header>
      <NameCard key={user.name} user={user} />
      <PasswordCard user={user} />
      <ModelAccessCard />
      <GmailCard />
      <BrowserCard />
    </div>
  );
}
