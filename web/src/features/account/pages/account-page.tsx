import { Link } from "react-router";

import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Section } from "@/components/blocks/section";
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

/** /account, one settings section per group: the model credential your agents use first, then
 * your name, your password, your Gmail where the server offers it (GmailCard hides itself
 * otherwise), the websites your browser is signed in to, and your other keys. */
export function AccountPage() {
  useTitle("Account");
  const { data: user } = useMe();
  // Inside RequireAuth there is always a user; the check keeps the types honest.
  if (!user) return null;
  return (
    <Page>
      <PageHeader
        title="Account"
        description="The model key your assistants run on, how you sign in, and the services the app reads for you."
        actions={
          <Button asChild variant="outline">
            <Link to={WELCOME}>Open the setup wizard</Link>
          </Button>
        }
      />
      <PageBody>
        <ModelAccessCard />
        <NameCard key={user.name} user={user} />
        <PasswordCard user={user} />
        <GmailCard />
        <BrowserCard />
        <Section
          layout="side"
          title="Other keys"
          description="Your own keys for the job feeds Find jobs reads."
        >
          <AdzunaKeyCard />
        </Section>
      </PageBody>
    </Page>
  );
}
