import { LoadError } from "@/components/blocks/load-error";
import { Page } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useApplySettings } from "@/features/apply/api/use-apply-settings";
import { useModelKey } from "@/features/agents/api/use-model-key";
import { useMe } from "@/features/auth/api/use-me";
import { useProfile } from "@/features/profile/api/use-profile";
import { useTitle } from "@/hooks/use-title";
import { WORDING } from "@/lib/wording";

import { useOnboarding } from "../api/use-onboarding";
import { Wizard } from "../components/wizard/wizard";
import { WELCOME_LINE } from "../helpers/words";

/**
 * /welcome, the first login wizard (opportunities O5): ten steps with a progress bar, Back,
 * Next and "Skip this step" (not on the roles nor the summary). Next saves the step through the
 * profile's own route with the revision this page read (a second tab's save shows the profile's
 * conflict dialog) and apply's settings route for the mode after a Like. What the person typed is
 * kept in one draft for the whole wizard, so Back shows it as left. The step open is in `?step=`
 * and the steps passed are kept in the profile (POST /api/opportunities/onboarding/steps), so a
 * reload, a closed tab or another device resumes at the right step. The summary shows what was
 * saved, never what was typed and then skipped.
 */
export function WelcomePage() {
  useTitle(WORDING.welcome);
  const me = useMe();
  const profile = useProfile();
  const state = useOnboarding();
  const settings = useApplySettings();
  // Read before the first step opens: a saved credential means Model access is done.
  const key = useModelKey();
  // A failed refetch keeps the wizard and its draft: only a first load that fails stops here.
  if ((profile.isError && !profile.data) || (state.isError && !state.data))
    return (
      <Page>
        <PageHeader title={WORDING.welcome} description={WELCOME_LINE} />
        <LoadError
          query={{
            error: profile.error ?? state.error,
            isFetching: profile.isFetching || state.isFetching,
            refetch: () => {
              void profile.refetch();
              void state.refetch();
            },
          }}
        >
          Your setup could not be loaded.
        </LoadError>
      </Page>
    );
  if (!profile.data || !state.data || !me.data || key.isPending)
    return (
      // No h1 yet: the title shows with the wizard, so whatever waits for it finds the steps.
      <Page>
        <div
          role="status"
          aria-live="polite"
          className="delayed-appear flex flex-col gap-6"
        >
          <span className="sr-only">Loading</span>
          <Skeleton aria-hidden="true" className="h-9 w-40" />
          <Skeleton aria-hidden="true" className="h-1.5 w-full" />
          <Skeleton aria-hidden="true" className="h-80" />
        </div>
      </Page>
    );
  return (
    <Wizard
      profile={profile.data}
      state={state.data}
      settings={settings.data}
    />
  );
}
