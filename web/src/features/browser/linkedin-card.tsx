/**
 * The LinkedIn switch of browser B10 (SPEC-browser decision 2, "Full use"), inside "Your
 * browser": off by default. Turning it on shows the api's one sentence on LinkedIn's terms and
 * the button "I accept, turn LinkedIn on", which records the acceptance of that version. A new
 * version of the sentence shows it again. The card also tells the person when LinkedIn is paused
 * after a security check, and when the last sweep met LinkedIn's sign in page. Edited search
 * words or location are saved with "Save search", while LinkedIn is on or off.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorDetail, request } from "@/lib/api";

const linkedinSchema = z.object({
  enabled: z.boolean(),
  accepted: z.boolean(),
  allowed: z.boolean(),
  text: z.string(),
  text_version: z.number(),
  accepted_at: z.string().nullable(),
  accepted_version: z.number().nullable(),
  query: z.string().nullable(),
  location: z.string().nullable(),
  paused_until: z.string().nullable(),
  last_sweep_at: z.string().nullable(),
  needs_signin: z.boolean(),
});
export type LinkedIn = z.infer<typeof linkedinSchema>;

type LinkedInIn = {
  enabled: boolean;
  accept_version?: number;
  query?: string;
  location?: string;
};

const linkedinKey = ["browser", "linkedin"] as const;

function getLinkedIn(signal?: AbortSignal): Promise<LinkedIn> {
  return request("/browser/linkedin", {
    signal,
    validate: (data) => linkedinSchema.parse(data),
  });
}

function putLinkedIn(body: LinkedInIn): Promise<LinkedIn> {
  return request("/browser/linkedin", {
    method: "PUT",
    body,
    validate: (data) => linkedinSchema.parse(data),
  });
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function LinkedInCard() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: linkedinKey,
    queryFn: ({ signal }) => getLinkedIn(signal),
  });
  const [asking, setAsking] = useState(false);
  const [words, setWords] = useState<string | null>(null);
  const [place, setPlace] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: putLinkedIn,
    onSuccess: (data) => {
      client.setQueryData(linkedinKey, data);
      setAsking(false);
      setWords(null);
      setPlace(null);
    },
  });
  const data = query.data;
  if (!data) return null;
  const keywords = words ?? data.query ?? "";
  const where = place ?? data.location ?? "";
  const edited =
    keywords.trim() !== (data.query ?? "") ||
    where.trim() !== (data.location ?? "");
  // A stale sentence (on, not accepted) is answered by the accept button, which sends the words.
  const canSave =
    edited && keywords.trim() !== "" && !(data.enabled && !data.accepted);
  const turnOn = () => {
    if (!data.accepted) {
      setAsking(true);
      return;
    }
    save.mutate({ enabled: true, query: keywords, location: where });
  };
  return (
    <section
      aria-labelledby="linkedin-title"
      className="flex flex-col gap-3 rounded-md border p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="linkedin-title" className="font-medium">
          LinkedIn
        </h3>
        <Button
          role="switch"
          aria-checked={data.enabled}
          aria-label="LinkedIn search"
          variant={data.enabled ? "default" : "outline"}
          size="sm"
          disabled={save.isPending}
          onClick={() => {
            if (data.enabled) save.mutate({ enabled: false });
            else turnOn();
          }}
        >
          {data.enabled ? "On" : "Off"}
        </Button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor="linkedin-query">Search words</Label>
          <Input
            id="linkedin-query"
            maxLength={200}
            value={keywords}
            onChange={(event) => {
              setWords(event.target.value);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="linkedin-location">Location</Label>
          <Input
            id="linkedin-location"
            maxLength={200}
            value={where}
            onChange={(event) => {
              setPlace(event.target.value);
            }}
          />
        </div>
      </div>
      {canSave ? (
        <div>
          <Button
            size="sm"
            variant="outline"
            disabled={save.isPending}
            onClick={() => {
              save.mutate({
                enabled: data.enabled,
                query: keywords,
                location: where,
              });
            }}
          >
            Save search
          </Button>
        </div>
      ) : null}
      {data.enabled && !data.accepted ? (
        <p className="text-sm">
          The sentence below changed. LinkedIn stays stopped until you accept
          it.
        </p>
      ) : null}
      {asking || (data.enabled && !data.accepted) ? (
        <div
          role="group"
          aria-label="LinkedIn's terms"
          className="flex flex-col gap-2"
        >
          <p className="text-sm">{data.text}</p>
          <div>
            <Button
              size="sm"
              disabled={save.isPending}
              onClick={() => {
                save.mutate({
                  enabled: true,
                  accept_version: data.text_version,
                  query: keywords,
                  location: where,
                });
              }}
            >
              I accept, turn LinkedIn on
            </Button>
          </div>
        </div>
      ) : null}
      {data.paused_until ? (
        <p role="status" className="text-sm">
          LinkedIn showed a security check, so all LinkedIn work is paused until{" "}
          {day(data.paused_until)}.
        </p>
      ) : null}
      {data.enabled && data.needs_signin && !data.paused_until ? (
        <p role="status" className="text-sm">
          LinkedIn needs you to sign in. Use Sign in to a site with
          linkedin.com, then the next sweep searches again.
        </p>
      ) : null}
      {save.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {errorDetail(save.error) ??
            "LinkedIn could not be changed. Try again."}
        </p>
      ) : null}
    </section>
  );
}
