import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, LoaderCircle, X } from "lucide-react";
import { useEffect, useState } from "react";

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
  endSession,
  getBrowser,
  startSession,
  type Browser,
  type QueuePlace,
  waitText,
} from "./browser-api";
import { ForgetDialog } from "./forget-dialog";
import { HandoffBanner } from "./handoff-banner";
import { LinkedInCard } from "./linkedin-card";
import { LiveBrowser } from "./live-browser";
import { SignInButton } from "./signin-button";

// A place in the queue is kept by asking again within 60 s (browser B3); the card asks every 20 s.
const KEEP_PLACE_MS = 20_000;
// While the browser starts, or someone else holds it, the card reads the state again this often.
const WAIT_POLL_MS = 3_000;

function mine(browser: Browser | undefined) {
  return browser?.holder === "you" ? browser.lease : null;
}

/** Account, "Your browser": the person's own browser on the server, and a session to look at it. */
export function BrowserCard() {
  const client = useQueryClient();
  const [queued, setQueued] = useState<QueuePlace | null>(null);
  const query = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    refetchInterval: (current) => {
      const lease = mine(current.state.data);
      return (queued && !lease) || lease?.state === "starting"
        ? WAIT_POLL_MS
        : false;
    },
  });
  const lease = mine(query.data);
  // Once the browser is ours the place in the queue is spent.
  const place = lease ? null : queued;

  const start = useMutation({
    mutationFn: () => startSession("look"),
    onSuccess: (session) => {
      setQueued(session.lease ? null : session.queue);
      void client.invalidateQueries({ queryKey: browserQueryKey });
    },
  });
  const end = useMutation({
    mutationFn: endSession,
    onSuccess: () => {
      setQueued(null);
      void client.invalidateQueries({ queryKey: browserQueryKey });
    },
  });

  // Waiting in the queue: ask again before the place lapses, until the browser is ours.
  const { mutate } = start;
  useEffect(() => {
    if (!place) return;
    const timer = window.setInterval(() => {
      mutate();
    }, KEEP_PLACE_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [place, mutate]);

  const failure = start.error ?? end.error ?? query.error;
  return (
    <Card aria-labelledby="browser-card-title">
      <CardHeader>
        <CardTitle>
          <h2 id="browser-card-title" className="text-lg">
            Your browser
          </h2>
        </CardTitle>
        <CardDescription>
          A browser of your own on the server, which keeps the sites you signed
          in to. The assistant uses it to apply for you, and you can watch it or
          take over at any moment.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <HandoffBanner />
        {query.isPending ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle aria-hidden className="size-4 animate-spin" />
            Loading your browser.
          </p>
        ) : lease?.state === "starting" ? (
          <p role="status" className="text-sm text-muted-foreground">
            Your browser is starting.
          </p>
        ) : lease ? (
          <LiveBrowser key={lease.id} person={!lease.automation} />
        ) : place ? (
          <p role="status" className="text-sm text-muted-foreground">
            {waitText(place)}
          </p>
        ) : query.data && !query.data.available ? (
          <p className="text-sm text-muted-foreground">
            The browser is not available right now.
          </p>
        ) : null}
        {failure && (
          <p role="alert" className="text-sm text-destructive">
            {errorDetail(failure) ?? describeError(failure)}
          </p>
        )}
        <LinkedInCard />
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        {lease ? (
          <Button
            type="button"
            variant="outline"
            disabled={end.isPending}
            onClick={() => {
              end.mutate();
            }}
          >
            <X aria-hidden />
            Close my browser
          </Button>
        ) : (
          <Button
            type="button"
            disabled={
              start.isPending || !!place || query.data?.available === false
            }
            onClick={() => {
              start.mutate();
            }}
          >
            <Eye aria-hidden />
            Look at my browser
          </Button>
        )}
        <SignInButton />
        {/* apply A18c: the "Complete my profile on a site" slot goes here. */}
        <ForgetDialog />
      </CardFooter>
    </Card>
  );
}
