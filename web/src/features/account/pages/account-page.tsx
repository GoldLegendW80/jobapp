import { Link } from "react-router";

import { PageHeader } from "@/components/blocks/page-header";
import { Button } from "@/components/ui/button";
import { ModelAccessCard } from "@/features/account/components/model-access-card";
import { useMe } from "@/features/auth/api/use-me";
import { BrowserCard } from "@/features/account/components/browser-card";
import { AdzunaKeyCard } from "@/components/shared/jobs/job-feeds-ui";
import { GmailCard } from "@/features/account/components/gmail-card";
import { WELCOME } from "@/components/shared/onboarding/onboarding-gate";
import { useTitle } from "@/hooks/use-title";

import { NameCard } from "../components/name-card";
import { PasswordCard } from "../components/password-card";

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
      <PageHeader
        title="Account"
        description="The model key the assistant runs on, your name, the password you sign in with, the websites you are signed in to and your other keys."
        actions={
          <Button asChild variant="secondary">
            <Link to={WELCOME}>Open the setup wizard</Link>
          </Button>
        }
      />
      <ModelAccessCard />
      <NameCard key={user.name} user={user} />
      <PasswordCard user={user} />
      <GmailCard />
      <BrowserCard />
      <AdzunaKeyCard />
    </div>
  );
}
