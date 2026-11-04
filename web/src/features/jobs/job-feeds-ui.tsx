/**
 * The job feeds in the interface: their lines in a sweep's summary (`SweepJobFeeds`) and Adzuna's
 * key on the Account page (`AdzunaKeyCard`). Find jobs lists a feed like any address (sources.ts).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Rss } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { describeError, errorDetail } from "@/lib/api";

import { formatDateTime } from "./format";
import {
  ADZUNA_SIGNUP,
  FEED_NAMES,
  adzunaOf,
  feedWords,
  getJobFeeds,
  isJobFeed,
  jobFeedsKey,
  removeAdzunaKey,
  saveAdzunaKey,
} from "./job-feeds";
import { plural } from "./selection";
import type { SweepSource } from "./sweeps-api";

function useJobFeeds() {
  return useQuery({
    queryKey: jobFeedsKey,
    queryFn: ({ signal }) => getJobFeeds(signal),
    retry: false,
  });
}

function feedLine(source: SweepSource): string {
  if (source.reason === "no_key" || source.error === "no_key")
    return feedWords("no_key");
  const searches =
    source.searches && source.searches > 0
      ? `${plural(source.searches, "search", "searches")}. `
      : "";
  const counts = `${searches}${plural(source.found, "posting")} kept, ${source.new.toLocaleString("en-GB")} new.`;
  return source.error ? `${counts} ${feedWords(source.error)}` : counts;
}

/** The job feeds of a sweep in its summary. */
export function SweepJobFeeds({ sources }: { sources: SweepSource[] }) {
  const feeds = sources.filter(
    (source) => source.mode === "api" && isJobFeed(source.kind),
  );
  if (!feeds.length) return null;
  return (
    <section aria-label="Job feeds" className="flex flex-col gap-1">
      <h3 className="flex items-center gap-1.5 text-sm font-medium">
        <Rss aria-hidden="true" className="size-4 text-muted-foreground" />
        Job feeds
      </h3>
      <ul className="flex flex-col gap-1 text-sm break-words">
        {feeds.map((feed, index) => (
          <li key={`${feed.kind}:${index}`}>
            <span className="font-medium">
              {isJobFeed(feed.kind) ? FEED_NAMES[feed.kind] : feed.kind}
            </span>
            <span className="text-muted-foreground">{`: ${feedLine(feed)}`}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Adzuna's key on the Account page: stored encrypted, never shown back. */
export function AdzunaKeyCard() {
  const id = useId();
  const client = useQueryClient();
  const feeds = useJobFeeds();
  const adzuna = adzunaOf(feeds.data);
  const [appId, setAppId] = useState("");
  const [appKey, setAppKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: saveAdzunaKey,
    onSuccess: () => {
      setAppId("");
      setAppKey("");
      setError(null);
      void client.invalidateQueries({ queryKey: jobFeedsKey });
    },
    onError: (failure) => {
      setError(errorDetail(failure) ?? describeError(failure));
    },
  });
  const remove = useMutation({
    mutationFn: removeAdzunaKey,
    onSuccess: () => {
      setError(null);
      void client.invalidateQueries({ queryKey: jobFeedsKey });
    },
    onError: (failure) => {
      setError(describeError(failure));
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!appId.trim() || !appKey.trim()) {
      setError("Paste both the application ID and the application key.");
      return;
    }
    save.mutate({ app_id: appId.trim(), app_key: appKey.trim() });
  };
  const busy = save.isPending || remove.isPending;
  return (
    <Card id="adzuna-key">
      <CardHeader>
        <CardTitle>Adzuna key</CardTitle>
        <CardDescription>
          Find jobs reads Adzuna (jobs in France) with your own free key. Create
          one at{" "}
          <a
            className="underline"
            href={ADZUNA_SIGNUP}
            target="_blank"
            rel="noopener noreferrer"
          >
            developer.adzuna.com
          </a>
          , then paste its application ID and key here. They are stored
          encrypted and never shown again.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm" role="status">
          {feeds.isPending
            ? "Checking whether a key is stored…"
            : feeds.isError
              ? "Whether a key is stored could not be checked."
              : adzuna?.key_stored
                ? `A key ending in ${adzuna.key_last4} is stored${adzuna.key_saved_at ? `, saved ${formatDateTime(adzuna.key_saved_at)}` : ""}.`
                : "No key is stored: Adzuna is skipped."}
        </p>
        {feeds.data && !feeds.data.keys_available ? (
          <p className="text-sm text-muted-foreground">
            This server cannot store keys yet: its CREDENTIALS_KEY is not set.
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
        <form noValidate onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor={`${id}-app-id`}>Application ID</Label>
            <Input
              id={`${id}-app-id`}
              autoComplete="off"
              spellCheck={false}
              maxLength={200}
              value={appId}
              onChange={(event) => {
                setAppId(event.target.value);
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${id}-app-key`}>Application key</Label>
            <Input
              id={`${id}-app-key`}
              type="password"
              autoComplete="off"
              spellCheck={false}
              maxLength={400}
              value={appKey}
              onChange={(event) => {
                setAppKey(event.target.value);
              }}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              {save.isPending ? "Saving…" : "Save key"}
            </Button>
            {adzuna?.key_stored ? (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  remove.mutate();
                }}
              >
                Remove key
              </Button>
            ) : null}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
