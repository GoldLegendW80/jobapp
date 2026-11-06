/**
 * A fake /api/profile/about for tests (api/app/profile/about_router.py): the document kept in
 * memory with a version per save, Done that starts a reading (finished by the test with
 * `state.read`), Apply, Undo and Not now. Invented data only.
 */
import { http, HttpResponse } from "msw";

import {
  keyOf,
  splitParts,
  type About,
  type AboutPart,
  type PartKind,
} from "@/features/profile/about-api";

import { server } from "./server";

export const ABOUT_TEXT = `## Skills

Years of experience: 5

### What I have
- data_tools: Python, SQL, dbt

## Projects

| Project | Year |
|---|---|
| Streaming platform | 2023 |

## Answers

### Notice period
Three months.

### Background checks
Yes, I consent to background checks.
`;

const KINDS: Record<string, PartKind> = {
  skills: "skills",
  answers: "answers",
};

export const COST = { currency: "USD", low: "0.01", high: "0.03" };

export interface FakeAbout {
  about: About;
  saves: { version: number; text: string }[];
  dones: { heading: string; version: number }[];
  steps: string[];
  /** Finish the running reading with these lines (and values not taken). */
  read: (lines: string[], refused?: string[]) => void;
}

export function aboutOf(
  text: string,
  options: { key?: "api_key" | "subscription" | null; version?: number } = {},
): About {
  const key = options.key === undefined ? "api_key" : options.key;
  const parts: AboutPart[] = splitParts(text).parts.map((part) => ({
    heading: part.heading,
    key: keyOf(part.heading),
    kind: KINDS[keyOf(part.heading)] ?? "facts",
    state: "in_step",
    duplicate: false,
    too_long: false,
    estimate:
      key === null
        ? null
        : {
            tokens: { low: 9000, high: 21000 },
            cost: key === "api_key" ? COST : null,
          },
  }));
  return {
    text,
    version: options.version ?? 0,
    updated_at: "2026-11-05T09:00:00Z",
    parts,
    reading: null,
    undo: null,
    needs: key === null ? "model_key" : null,
    advanced: 0,
    missing_blocks: [],
    dones_left: 30,
    part_max: 20000,
    text_max: 300000,
  };
}

/** Install the fake. `key`: the kind of model credential, null for none. */
export function fakeAbout(
  options: { text?: string; key?: "api_key" | "subscription" | null } = {},
): FakeAbout {
  const state: FakeAbout = {
    about: aboutOf(options.text ?? ABOUT_TEXT, options),
    saves: [],
    dones: [],
    steps: [],
    read: (lines, refused = []) => {
      const reading = state.about.reading;
      if (!reading) throw new Error("no reading runs");
      state.about = {
        ...state.about,
        reading: {
          ...reading,
          status: "ready",
          lines,
          refused,
          can_apply: lines.length > 0,
        },
      };
    },
  };
  const json = () => HttpResponse.json(state.about);
  server.use(
    http.get("*/api/profile/about", json),
    http.put("*/api/profile/about", async ({ request }) => {
      const body = (await request.json()) as { version: number; text: string };
      state.saves.push(body);
      if (body.version !== state.about.version)
        return HttpResponse.json(
          {
            detail: "This document changed in another tab.",
            current: state.about,
          },
          { status: 409 },
        );
      const parts = aboutOf(body.text, options).parts.map((part) => {
        const before = state.about.parts.find((one) => one.key === part.key);
        const same = splitParts(state.about.text).parts.find(
          (one) => keyOf(one.heading) === part.key,
        );
        const changed =
          same?.body !==
          splitParts(body.text).parts.find(
            (one) => keyOf(one.heading) === part.key,
          )?.body;
        return {
          ...part,
          state: !before ? "new" : changed ? "edited" : before.state,
        };
      });
      state.about = {
        ...state.about,
        text: body.text,
        version: state.about.version + 1,
        parts,
      };
      return json();
    }),
    http.post("*/api/profile/about/done", async ({ request }) => {
      const body = (await request.json()) as {
        heading: string;
        version: number;
      };
      state.dones.push(body);
      const kind = state.about.parts.find(
        (part) => part.key === keyOf(body.heading),
      )?.kind;
      if (kind === "facts") {
        // Read by code and applied at once, as the api does.
        state.about = {
          ...state.about,
          reading: null,
          undo: {
            heading: body.heading,
            lines: [`1 fact block updated: ${body.heading}`],
          },
          parts: state.about.parts.map((part) =>
            part.key === keyOf(body.heading)
              ? { ...part, state: "in_step" }
              : part,
          ),
        };
        return json();
      }
      if (state.about.needs === "model_key")
        return HttpResponse.json(
          { detail: "Add a model key on the Account page first." },
          { status: 409 },
        );
      state.about = {
        ...state.about,
        reading: {
          heading: body.heading,
          key: keyOf(body.heading),
          status: "running",
          error: null,
          lines: [],
          refused: [],
          can_apply: false,
          stale: false,
        },
      };
      return json();
    }),
    http.post("*/api/profile/about/:step", ({ params }) => {
      const step = String(params.step);
      state.steps.push(step);
      const reading = state.about.reading;
      if (step === "apply" && reading) {
        state.about = {
          ...state.about,
          reading: null,
          undo: { heading: reading.heading, lines: reading.lines },
          parts: state.about.parts.map((part) =>
            part.key === reading.key ? { ...part, state: "in_step" } : part,
          ),
        };
      } else if (step === "undo") {
        state.about = { ...state.about, undo: null };
      } else if (step === "discard") {
        state.about = { ...state.about, reading: null };
      }
      return json();
    }),
  );
  return state;
}
