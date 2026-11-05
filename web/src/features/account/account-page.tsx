import { Link } from "react-router";

import { ModelAccessCard } from "@/features/agents/model-access-card";
import { useMe } from "@/features/auth/auth-context";
import { BrowserCard } from "@/features/browser/browser-card";
import { AdzunaKeyCard } from "@/features/jobs/job-feeds-ui";
import { GmailCard } from "@/features/mail/gmail-card";
import { WELCOME } from "@/features/onboarding/onboarding-gate";
import { useTitle } from "@/lib/use-title";

import { NameCard } from "./name-card";
import { PasswordCard } from "./password-card";

/** /account: the model credential your agents use first, then your name, your password, your
 * Gmail where the server offers it (GmailCard hides itself otherwise), the websites your browser
 * is signed in to, and your other keys. */
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
          The model key the assistant runs on, your name, the password you sign
          in with, the websites you are signed in to and your other keys.
        </p>
        <p className="text-sm">
          <Link className="underline underline-offset-4" to={WELCOME}>
            Open the setup wizard
          </Link>
        </p>
      </header>
      <ModelAccessCard />
      <NameCard key={user.name} user={user} />
      <PasswordCard user={user} />
      <GmailCard />
      <BrowserCard />
      <AdzunaKeyCard />
    </div>
  );
}
