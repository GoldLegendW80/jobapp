import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { describeError, errorDetail, request } from "@/lib/api";

import { browserQueryKey, browserSchema, getBrowser } from "./browser-api";
import { LiveBrowser } from "./live-browser";

const sessionSchema = z.object({
  lease: browserSchema.shape.lease,
  queue: z.object({ position: z.number() }).nullable(),
});

/**
 * Only the server's registered owner supplies the address and resumes the waiting work.
 * `heading` false: no line of its own naming the site and what to do, for a place that already
 * says both above it (the job page's Needs you alert).
 */
export function LaterHandoffBanner({
  module,
  refId,
  address,
  heading = true,
}: {
  module: string;
  refId: string;
  address: string;
  heading?: boolean;
}) {
  const client = useQueryClient();
  const [leaseId, setLeaseId] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const body = { module, ref: refId };
  const browser = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    enabled: leaseId !== null,
    refetchInterval: leaseId !== null ? 2_000 : false,
  });
  const opened = useMutation({
    mutationFn: () =>
      request("/browser/handoff/later", {
        method: "POST",
        body,
        timeoutMs: 40_000,
        validate: (data) => sessionSchema.parse(data),
      }),
    onSuccess: (session) => {
      setLeaseId(session.lease?.id ?? null);
      setWaiting(session.queue !== null);
      void client.invalidateQueries({ queryKey: browserQueryKey });
    },
  });
  const answer = useMutation({
    mutationFn: (action: "done" | "cancel") =>
      request(`/browser/handoff/later/${action}`, { method: "POST", body }),
    onSuccess: () => {
      setLeaseId(null);
      setWaiting(false);
      void client.invalidateQueries();
    },
  });
  let host = "the site";
  try {
    host = new URL(address).hostname || host;
  } catch {
    /* Server validates before opening. */
  }
  const lease = browser.data?.lease;
  const live = leaseId !== null && (browser.isPending || lease?.id === leaseId);
  const expired =
    leaseId !== null && !browser.isPending && lease?.id !== leaseId;
  const busy = opened.isPending || answer.isPending;
  const error = opened.error ?? answer.error;
  return (
    <section
      aria-label={`Sign in to ${host}`}
      className="flex min-w-0 flex-col gap-3 rounded-md border border-amber-500/60 p-3 [overflow-wrap:anywhere]"
    >
      {heading ? (
        <>
          <p className="font-medium">Needs you: sign in to {host}</p>
          <p className="text-sm">
            Sign in or sign up yourself in the live view, then click I&apos;m
            signed in. The form fills again and waits for your approval.
          </p>
        </>
      ) : null}
      {live ? (
        <LiveBrowser person status="Sign in, then click I'm signed in." />
      ) : null}
      {expired ? (
        <p role="status">
          The sign in session ended. The item is still waiting. Open it again to
          continue.
        </p>
      ) : null}
      {waiting ? (
        <p role="status">
          The browser is busy. Open the live view again when it is free.
        </p>
      ) : null}
      {error ? (
        <p role="alert">{errorDetail(error) ?? describeError(error)}</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {!live ? (
          <Button
            disabled={busy}
            onClick={() => {
              opened.mutate();
            }}
          >
            Open the live view to sign in
          </Button>
        ) : (
          <>
            <Button
              disabled={busy}
              onClick={() => {
                answer.mutate("done");
              }}
            >
              I&apos;m signed in
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                answer.mutate("cancel");
              }}
            >
              Cancel
            </Button>
          </>
        )}
      </div>
    </section>
  );
}
