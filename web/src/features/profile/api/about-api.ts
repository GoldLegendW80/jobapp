/**
 * The "About you" document (/api/profile/about, api/app/profile/about_router.py). GET never calls
 * a model: it answers the document, the state of each part, the last reading with what it would
 * change, and the estimate of a Done on each part. PUT saves the text as typed (a stale version
 * answers 409 with the current document); POST /done reads one part with the model; /apply,
 * /undo, /discard and /rewrite act on the last reading and on a part.
 *
 * `splitParts` and `joinParts` are the twins of api/app/profile/about_text.py `split` and `join`:
 * a `## Heading` line outside a code fence starts a part.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { isApiError, request } from "@/lib/api";

const decimal = z.union([z.string(), z.number()]);

const estimateSchema = z.object({
  tokens: z.object({ low: z.number(), high: z.number() }),
  cost: z
    .object({ currency: z.string(), low: decimal, high: decimal })
    .nullable(),
});

export const PART_KINDS = ["skills", "answers", "facts"] as const;
export type PartKind = (typeof PART_KINDS)[number];

export const aboutSchema = z.object({
  text: z.string(),
  version: z.number(),
  updated_at: z.string(),
  parts: z.array(
    z.object({
      heading: z.string(),
      key: z.string(),
      kind: z.enum(PART_KINDS),
      /** in_step: the profile says what the part says; edited: changed since; elsewhere: the
       * profile changed outside the document; new: never applied */
      state: z.enum(["in_step", "edited", "elsewhere", "new"]),
      duplicate: z.boolean(),
      too_long: z.boolean(),
      /** Null without a model key; `cost` on an API key only. */
      estimate: estimateSchema.nullable(),
    }),
  ),
  reading: z
    .object({
      heading: z.string(),
      key: z.string(),
      status: z.enum(["running", "ready", "failed"]),
      error: z.string().nullable(),
      lines: z.array(z.string()),
      refused: z.array(z.string()),
      can_apply: z.boolean(),
      /** the part changed since Done: Apply is refused */
      stale: z.boolean(),
    })
    .nullable(),
  undo: z
    .object({ heading: z.string(), lines: z.array(z.string()) })
    .nullable(),
  needs: z.enum(["model_key"]).nullable(),
  /** regular expression categories and gates, which the document does not hold */
  advanced: z.number(),
  /** fact blocks of the profile no part holds */
  missing_blocks: z.array(z.string()),
  dones_left: z.number(),
  part_max: z.number(),
  text_max: z.number(),
});
export type About = z.infer<typeof aboutSchema>;
export type AboutPart = About["parts"][number];
export type Reading = NonNullable<About["reading"]>;

export const aboutKey = ["profile-about"] as const;
/** How often the page asks again while a part is being read. */
export const READING_POLL_MS = 2000;

const validate = (data: unknown) => aboutSchema.parse(data);

export function getAbout(signal?: AbortSignal): Promise<About> {
  return request("/profile/about", { signal, validate });
}

export function saveAbout(version: number, text: string): Promise<About> {
  return request("/profile/about", {
    method: "PUT",
    body: { version, text },
    validate,
  });
}

/** The current document a 409 answered (another tab saved first), or undefined. */
export function staleAbout(error: unknown): About | undefined {
  if (!isApiError(error) || error.status !== 409) return undefined;
  const parsed = z.object({ current: aboutSchema }).safeParse(error.body);
  return parsed.success ? parsed.data.current : undefined;
}

export function doneAbout(heading: string, version: number): Promise<About> {
  return request("/profile/about/done", {
    method: "POST",
    body: { heading, version },
    validate,
  });
}

export function actOnReading(
  step: "apply" | "undo" | "discard",
): Promise<About> {
  return request(`/profile/about/${step}`, { method: "POST", validate });
}

/** Write a part again from the profile; without a heading, add the blocks no part holds. */
export function rewriteAbout(
  heading: string | null,
  version: number,
): Promise<About> {
  return request("/profile/about/rewrite", {
    method: "POST",
    body: heading === null ? { version } : { heading, version },
    validate,
  });
}

export function useAbout() {
  return useQuery({
    queryKey: aboutKey,
    queryFn: ({ signal }) => getAbout(signal),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.reading?.status === "running" ? READING_POLL_MS : false,
  });
}

/** Reads "About you" again, after a change made to the profile around it (an import, a restore). */
export function useRefreshAbout() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: aboutKey });
  };
}

// --- the parts, as the api reads them -----------------------------------------------------------

export interface Part {
  heading: string;
  body: string;
}

const PART = /^##[ \t]+(\S.*?)[ \t]*$/;
const TOP = /^#{1,2}[ \t]+(?=\S)/;
const FENCE = /^\s*(```|~~~)/;

export function keyOf(heading: string): string {
  return heading
    .normalize("NFKC")
    .toLocaleLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function splitParts(text: string): { preamble: string; parts: Part[] } {
  const preamble: string[] = [];
  const parts: { heading: string; lines: string[] }[] = [];
  let fence: string | null = null;
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    const opened = FENCE.exec(line);
    if (fence !== null) {
      if (opened && opened[1] === fence) fence = null;
    } else if (opened) {
      fence = opened[1] ?? null;
    } else {
      const heading = PART.exec(line);
      if (heading) {
        const raw = heading[1] ?? "";
        parts.push({ heading: raw.replace(/[#\s]+$/, "") || raw, lines: [] });
        continue;
      }
    }
    (parts.at(-1)?.lines ?? preamble).push(line);
  }
  const trim = (lines: string[]) => lines.join("\n").replace(/^\n+|\n+$/g, "");
  return {
    preamble: trim(preamble),
    parts: parts.map((part) => ({
      heading: part.heading,
      body: trim(part.lines),
    })),
  };
}

/** Level one and two headings of a body as level three: they would start a part. */
export function demote(body: string): string {
  let fence: string | null = null;
  return body
    .split("\n")
    .map((line) => {
      const opened = FENCE.exec(line);
      if (fence !== null) {
        if (opened && opened[1] === fence) fence = null;
        return line;
      }
      if (opened) {
        fence = opened[1] ?? null;
        return line;
      }
      return TOP.test(line) ? line.replace(TOP, "### ") : line;
    })
    .join("\n");
}

export function joinParts(preamble: string, parts: readonly Part[]): string {
  const chunks = preamble.trim() ? [preamble.replace(/^\n+|\n+$/g, "")] : [];
  for (const part of parts) {
    chunks.push(`## ${part.heading}\n\n${part.body}`.trimEnd());
  }
  return `${chunks.join("\n\n")}\n`;
}
