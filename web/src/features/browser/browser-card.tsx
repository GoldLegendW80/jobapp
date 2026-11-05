import { useQuery } from "@tanstack/react-query";
import { LoaderCircle, RotateCw } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { AdvancedSection } from "@/components/advanced-section";
import { SiteProfileSlot } from "@/features/apply/site-profile/site-profile-slot";
import { formatDate } from "@/features/jobs/format";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { describeError, errorDetail } from "@/lib/api";

import {
  browserQueryKey,
  getBrowser,
  personOwnsInput,
  type Browser,
} from "./browser-api";
import { ForgetDialog } from "./forget-dialog";
import { HandoffBanner } from "./handoff-banner";
import { LiveBrowser } from "./live-browser";
import { SiteSignIn } from "./site-signin";
import { getSiteLogins, siteLoginsQueryKey } from "./site-logins-api";
import { handoffOf } from "./use-handoff";

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
  const query = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    refetchInterval: (current) =>
      assistant(current.state.data)?.state === "starting"
        ? WAIT_POLL_MS
        : false,
  });
  const logins = useQuery({
    queryKey: siteLoginsQueryKey,
    queryFn: ({ signal }) => getSiteLogins(signal),
  });
  const lease = assistant(query.data);
  // A sign in hand off shows its own live view, with the person at the keyboard, in the banner.
  const handoff = handoffOf(query.data);
  const sites = logins.data ?? [];

  return (
    <Card aria-labelledby="browser-card-title">
      <CardHeader>
        <CardTitle>
          <h2 id="browser-card-title" className="text-lg">
            {TITLE}
          </h2>
        </CardTitle>
        <CardDescription>
          Your own browser on the server keeps these logins: the searches read
          the websites signed in, for you only. The app saves no password.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
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
        {logins.isPending ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle aria-hidden className="size-4 animate-spin" />
            Loading your websites.
          </p>
        ) : logins.isError && !logins.data ? (
          <div className="flex flex-col items-start gap-2">
            <p role="alert" className="text-sm text-destructive">
              Your websites could not be read.{" "}
              {errorDetail(logins.error) ?? describeError(logins.error)}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={logins.isFetching}
              onClick={() => {
                void logins.refetch();
              }}
            >
              <RotateCw aria-hidden />
              Try again
            </Button>
          </div>
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
          <ul
            aria-labelledby="browser-card-title"
            className="divide-y rounded-md border"
          >
            {sites.map((site) => (
              <li
                key={site.host}
                className="flex min-w-0 flex-col gap-2 p-3 text-sm sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="font-medium break-all">{site.host}</span>
                  {site.state === "signed_in" ? (
                    <span className="text-xs text-muted-foreground">
                      Since {formatDate(site.signed_in_at)}
                    </span>
                  ) : null}
                </div>
                <div className="flex min-w-0 sm:max-w-[60%] sm:justify-end">
                  <SiteSignIn url={`https://${site.host}/`} forget={false} />
                </div>
              </li>
            ))}
          </ul>
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
      </CardContent>
      <CardFooter className="flex flex-col items-start gap-4">
        <ForgetDialog
          profile={query.data?.profile}
          onForgotten={() => {
            setForgotten(true);
          }}
        />
        <AdvancedSection
          title={PROFILE_TITLE}
          open={profileOpen}
          onOpenChange={setProfileOpen}
          className="w-full"
        >
          <SiteProfileSlot />
        </AdvancedSection>
      </CardFooter>
    </Card>
  );
}
