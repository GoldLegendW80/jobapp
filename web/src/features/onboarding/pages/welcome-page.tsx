import { PageLoading, PageUnavailable } from "@/components/blocks/page-status";
import { useApplySettings } from "@/features/apply/api/use-apply-settings";
import { useMe } from "@/features/auth/api/use-me";
import { useProfile } from "@/features/profile/api/use-profile";
import { useTitle } from "@/hooks/use-title";
import { WORDING } from "@/lib/wording";

import { useOnboarding } from "../api/use-onboarding";
import { Wizard } from "../components/wizard/wizard";

/**
 * /welcome, the first login wizard (opportunities O5): eight steps with a progress bar, Back,
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
  // A failed refetch keeps the wizard and its draft: only a first load that fails stops here.
  if ((profile.isError && !profile.data) || (state.isError && !state.data))
    return (
      <PageUnavailable
        error={profile.error ?? state.error}
        onRetry={() => {
          void profile.refetch();
          void state.refetch();
        }}
        retrying={profile.isFetching || state.isFetching}
      />
    );
  if (!profile.data || !state.data || !me.data) return <PageLoading />;
  return (
    <Wizard
      profile={profile.data}
      state={state.data}
      settings={settings.data}
    />
  );
}
