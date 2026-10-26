import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, LogIn } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
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
 * person's browser only. "Forget all logins" (B7) removes every website's login.
 */
export function SiteSignIn({ url }: { url: string }) {
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
              <LoaderCircle aria-hidden className="animate-spin" />
            ) : null}
            Done
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-label={`Sign in to ${host}`}
            disabled={start.isPending}
            onClick={() => {
              setNote(null);
              setOpened(true);
              start.mutate();
            }}
          >
            <LogIn aria-hidden />
            {login?.state === "expired" ? "Sign in again" : "Sign in"}
          </Button>
        )}
        {login ? (
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
