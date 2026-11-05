import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LogIn } from "lucide-react";
import { Fragment, useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Item,
  ItemContent,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { describeError, errorDetail } from "@/lib/api";

import {
  browserQueryKey,
  getBrowser,
  personOwnsInput,
  startSession,
  waitText,
  type QueuePlace,
  type Session,
} from "./browser-api";
import { ForgetDialog } from "./forget-dialog";
import { LiveBrowser } from "./live-browser";
import {
  getSiteLogins,
  hostOf,
  siteDone,
  siteLoginsQueryKey,
} from "./site-logins-api";

// A place in the queue is kept by asking again within 60 s (browser B3).
const KEEP_PLACE_MS = 20_000;
const WAIT_POLL_MS = 3_000;
export const SIGN_IN_HINT =
  "Your browser opens on this website with no assistant connected. Sign in or create your account yourself, then click Done. The app saves no password.";
export const EXPIRED =
  "Your login on this website has ended. Sign in again so the search can read it.";

/**
 * A job website's Sign in (browser B17): the person's own browser opens on the website in the
 * live view, the person signs in themselves, and Done ends the session and checks that the
 * browser kept the website's login. The searches then read that website signed in, in this
 * person's browser only. "Forget all logins" (B7) removes every website's login. `optional`
 * (a line of Find jobs) says, before any login, that signing in is only for a website that shows
 * more postings to members.
 */
export function SiteSignIn({
  url,
  forget = true,
  optional = false,
}: {
  url: string;
  /** false: no "Forget all logins" of its own (Account's list has one for every website). */
  forget?: boolean;
  /** true in Find jobs: the button reads "Sign in (optional)" with a one line hint. */
  optional?: boolean;
}) {
  const hintId = useId();
  const host = hostOf(url);
  const client = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [place, setPlace] = useState<QueuePlace | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const logins = useQuery({
    queryKey: siteLoginsQueryKey,
    queryFn: ({ signal }) => getSiteLogins(signal),
  });
  const browser = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    enabled: opened,
    refetchInterval: (current) => {
      const lease = current.state.data?.lease;
      return opened && (!lease || lease.state === "starting")
        ? WAIT_POLL_MS
        : false;
    },
  });
  const lease =
    opened && browser.data?.holder === "you" ? browser.data.lease : null;
  const signingIn = lease?.purpose === "signin" ? lease : null;

  const took = (session: Session) => {
    setPlace(session.lease || !session.queue ? null : session.queue);
    void client.invalidateQueries({ queryKey: browserQueryKey });
  };
  const start = useMutation({
    mutationFn: () => startSession("signin", url),
    onSuccess: took,
  });
  const done = useMutation({
    mutationFn: () => siteDone(url),
    onSuccess: (answer) => {
      setOpened(false);
      setPlace(null);
      setNote(answer.state === "signed_in" ? null : answer.detail);
      void client.invalidateQueries({ queryKey: browserQueryKey });
    },
  });

  // Waiting in the queue: ask again before the place lapses, until the browser is ours.
  const { mutate } = start;
  useEffect(() => {
    if (!place || signingIn) return;
    const timer = window.setInterval(() => {
      mutate();
    }, KEEP_PLACE_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [place, signingIn, mutate]);

  if (!host) return null;
  const login = logins.data?.find((site) => site.host === host);
  const failure = start.error ?? done.error;
  const offered = optional && !login && !opened;
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {login?.state === "signed_in" ? (
          <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
            <Check aria-hidden className="size-4" />
            Signed in
          </span>
        ) : null}
        {opened ? (
          <Button
            type="button"
            size="sm"
            disabled={done.isPending}
            aria-busy={done.isPending}
            onClick={() => {
              done.mutate();
            }}
          >
            {done.isPending ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : null}
            Done
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-label={`Sign in to ${host}${offered ? " (optional)" : ""}`}
            aria-describedby={offered ? hintId : undefined}
            disabled={start.isPending}
            onClick={() => {
              setNote(null);
              setOpened(true);
              start.mutate();
            }}
          >
            <LogIn aria-hidden />
            {login?.state === "expired"
              ? "Sign in again"
              : offered
                ? "Sign in (optional)"
                : "Sign in"}
          </Button>
        )}
        {offered ? (
          <span id={hintId} className="text-xs text-muted-foreground">
            Only if this site shows more postings to members.
          </span>
        ) : null}
        {login && forget ? (
          <ForgetDialog
            profile={browser.data?.profile}
            onForgotten={() => {
              setNote(null);
            }}
          />
        ) : null}
      </div>
      {login?.state === "expired" && !opened ? (
        <p className="text-sm text-muted-foreground">{EXPIRED}</p>
      ) : null}
      {opened ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">{SIGN_IN_HINT}</p>
          {signingIn?.state === "active" ||
          signingIn?.state === "waiting_person" ? (
            <LiveBrowser
              key={`${signingIn.id}:${personOwnsInput(signingIn)}`}
              person={personOwnsInput(signingIn)}
            />
          ) : place ? (
            <p role="status" className="text-sm text-muted-foreground">
              {waitText(place)}
            </p>
          ) : (
            <p role="status" className="text-sm text-muted-foreground">
              Your browser is starting.
            </p>
          )}
        </div>
      ) : null}
      {note ? (
        <p role="status" className="text-sm text-muted-foreground">
          {note}
        </p>
      ) : null}
      {failure ? (
        <p role="alert" className="text-sm text-destructive">
          {errorDetail(failure) ?? describeError(failure)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The job websites of Find jobs in the profile's Search tab (browser B17), each with its Sign
 * in: the same action as in the Find jobs setup, for the saved websites.
 */
export function JobWebsitesSignIn({ urls }: { urls: string[] }) {
  if (!urls.length) return null;
  return (
    <section aria-label="Sign in to your job websites" className="space-y-2">
      <h3 className="font-medium">Sign in to your job websites</h3>
      <p className="text-sm text-muted-foreground">
        For a website of Find jobs that shows its jobs to members only: sign in
        once, and the next searches read it signed in, in your own browser only.
      </p>
      <ItemGroup className="rounded-md border">
        {urls.map((url, index) => (
          <Fragment key={`${url}:${index}`}>
            {index > 0 ? <ItemSeparator /> : null}
            <Item role="listitem" className="min-w-0 gap-2 rounded-none p-2">
              <ItemContent className="min-w-0">
                <ItemTitle className="w-auto font-normal break-all">
                  {url}
                </ItemTitle>
              </ItemContent>
              <SiteSignIn url={url} />
            </Item>
          </Fragment>
        ))}
      </ItemGroup>
    </section>
  );
}
