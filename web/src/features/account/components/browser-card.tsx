import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { LoadError } from "@/components/blocks/load-error";
import { SiteProfileSlot } from "@/features/account/components/site-profile/site-profile-slot";
import { formatDate } from "@/features/jobs/helpers/format";

import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { describeError, errorDetail } from "@/lib/api";

import {
  personOwnsInput,
  type Browser,
} from "@/features/browser/api/browser-api";
import { SettingsSection } from "@/components/shared/profile/settings-frame";
import { ForgetDialog } from "@/components/shared/browser/forget-dialog";
import { HandoffBanner } from "@/components/shared/browser/handoff-banner";
import { LiveBrowser } from "@/components/shared/browser/live-browser";
import { SiteSignIn } from "@/components/shared/browser/site-signin";
import { useBrowser } from "@/features/browser/api/use-browser";
import { handoffOf } from "@/features/browser/api/use-handoff";
import { useSiteLogins } from "@/features/browser/api/use-site-logins";

// While an assistant's lease starts, the card reads the state again this often.
const WAIT_POLL_MS = 3_000;

export const TITLE = "Websites you are signed in to";
const FORGOTTEN =
  "Your logins are forgotten. Your browser starts empty next time.";
export const PROFILE_TITLE = "Complete my profile on a website";

/** An assistant's lease of this person: what the card watches. The person's own sessions (a
 * sign in) show their live view where they were started. */
function assistant(browser: Browser | undefined) {
  const lease = browser?.holder === "you" ? browser.lease : null;
  return lease?.automation ? lease : null;
}

/**
 * Account, "Websites you are signed in to" (owner, audit chapter 2: "Only websites we are
 * signin"): the websites the person's own browser on the server keeps a login of (browser B17,
 * GET /api/browser/sites), each with its Sign in again (the Find jobs line's action), and
 * "Forget all logins" (B7) with its confirmation. "Complete my profile on a website" (apply A18)
 * is folded under it, open when a page sent the person here with `?profile_site=`. While an
 * assistant uses the browser, its live view (B4) and a hand off waiting for the person (B12b)
 * show here too, so a wait can be answered from this page.
 */
export function BrowserCard() {
  const [params] = useSearchParams();
  const [forgotten, setForgotten] = useState(false);
  const [profileOpen, setProfileOpen] = useState(() =>
    params.has("profile_site"),
  );
  const query = useBrowser({
    refetchInterval: (current) =>
      assistant(current.state.data)?.state === "starting"
        ? WAIT_POLL_MS
        : false,
  });
  const logins = useSiteLogins();
  const lease = assistant(query.data);
  // A sign in hand off shows its own live view, with the person at the keyboard, in the banner.
  const handoff = handoffOf(query.data);
  const sites = logins.data ?? [];

  return (
    <SettingsSection
      title={TITLE}
      description="Your own browser on the server keeps these logins: the searches read the websites signed in, for you only. The app saves no password."
      actions={
        <ForgetDialog
          profile={query.data?.profile}
          onForgotten={() => {
            setForgotten(true);
          }}
        />
      }
    >
      <HandoffBanner liveOpen />
      {lease?.state === "starting" ? (
        <p role="status" className="text-sm text-muted-foreground">
          Your browser is starting.
        </p>
      ) : lease && !handoff ? (
        <LiveBrowser
          key={`${lease.id}:${personOwnsInput(lease)}`}
          person={personOwnsInput(lease)}
        />
      ) : query.data && !query.data.available ? (
        <p className="text-sm text-muted-foreground">
          The browser is not available right now.
        </p>
      ) : null}
      {query.data?.browsers ? (
        <p className="text-sm text-muted-foreground" data-testid="browsers">
          Your searches and applications use up to {query.data.browsers}{" "}
          {query.data.browsers === 1 ? "browser" : "browsers"} at once. An admin
          sets this.
        </p>
      ) : null}
      {logins.isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner aria-hidden="true" role={undefined} />
          Loading your websites.
        </p>
      ) : logins.isError && !logins.data ? (
        <LoadError query={logins}>Your websites could not be read.</LoadError>
      ) : sites.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="no-sites">
          None yet. You sign in to a website from its line in{" "}
          <Link
            className="font-medium text-foreground underline underline-offset-4"
            to="/jobs?find=1"
          >
            Find jobs
          </Link>
          .
        </p>
      ) : (
        <ItemGroup aria-label={TITLE}>
          {sites.map((site, index) => (
            <Fragment key={site.host}>
              {index > 0 ? <ItemSeparator /> : null}
              <Item
                role="listitem"
                className="min-w-0 flex-col items-stretch gap-2 rounded-none px-0 py-3 sm:flex-row sm:flex-nowrap sm:items-start sm:justify-between"
              >
                <ItemContent className="min-w-0 flex-initial gap-0">
                  <ItemTitle className="w-auto break-all">
                    {site.host}
                  </ItemTitle>
                  {site.state === "signed_in" ? (
                    <ItemDescription className="text-xs">
                      Since {formatDate(site.signed_in_at)}
                    </ItemDescription>
                  ) : null}
                </ItemContent>
                <ItemActions className="min-w-0 sm:max-w-[60%] sm:justify-end">
                  <SiteSignIn url={`https://${site.host}/`} forget={false} />
                </ItemActions>
              </Item>
            </Fragment>
          ))}
        </ItemGroup>
      )}
      {forgotten && (
        <p role="status" className="text-sm text-muted-foreground">
          {FORGOTTEN}
        </p>
      )}
      {query.error ? (
        <p role="alert" className="text-sm text-destructive">
          {errorDetail(query.error) ?? describeError(query.error)}
        </p>
      ) : null}
      <AdvancedSection
        title={PROFILE_TITLE}
        open={profileOpen}
        onOpenChange={setProfileOpen}
        className="w-full"
      >
        <SiteProfileSlot />
      </AdvancedSection>
    </SettingsSection>
  );
}
