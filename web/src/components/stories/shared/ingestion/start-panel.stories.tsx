import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { StartPanel } from "@/components/shared/ingestion/run-start";
import {
  camilleEstimate,
  fakeCamilleDocuments,
} from "@/design-system/fixtures/camille-ingestion";
import {
  CONSENT_ASKED,
  CONSENT_GIVEN,
  fakeShownRuns,
} from "@/design-system/fixtures/shared-ingestion";
import { withApp } from "@/design-system/with-app";
import { fakeKeyEstimate, fakeRuns, QUOTE_B } from "@/test/ingestion-runs";
import { server } from "@/test/server";

const canvas = (element: HTMLElement) => within(element);

const tick = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await canvas(canvasElement).findByRole("checkbox", {
      name: "I have read this and I agree to send the selected documents",
    }),
  );
};

const unfold = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await canvas(canvasElement).findByRole("button", {
      name: "What is read, and the limits",
    }),
  );
};

const start = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  const button = await canvas(canvasElement).findByRole("button", {
    name: /^Complete my profile \(about/,
  });
  await userEvent.click(button);
};

const meta = {
  title: "Shared/Ingestion/Start panel",
  component: StartPanel,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The start of a run for chosen documents: one button that carries the token estimate, the consent box the first time, why the run cannot start, and folded, what is read and the limits. In Run start, the CV upload panel and the welcome wizard's profile step.",
      },
    },
  },
  args: { documentIds: ["doc-cv", "doc-notes"] },
  beforeEach: () => {
    fakeShownRuns([], CONSENT_ASKED);
  },
} satisfies Meta<typeof StartPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The first run: what is sent and to whom, and the button waits for the box. */
export const ConsentAsked: Story = {
  name: "Consent asked before the first run",
};

/** The box ticked for this version of the text: the button can start the run. */
export const ConsentTicked: Story = {
  name: "Consent ticked, ready to start",
  play: tick,
};

/** The consent given before: a reminder of where the documents go, under the button. */
export const ConsentGiven: Story = {
  name: "Consent given before, the reminder",
  beforeEach: () => {
    fakeShownRuns([], CONSENT_GIVEN);
  },
};

/** On an API key: the cost range under the button, as an estimate. */
export const OnAnApiKey: Story = {
  name: "On an API key, with the cost",
  beforeEach: () => {
    fakeCamilleDocuments();
    fakeRuns({ consent: CONSENT_GIVEN, estimate: fakeKeyEstimate() });
  },
};

/** No model credential: the run cannot start, why, and where to add one. */
export const Blocked: Story = {
  name: "Blocked, no model credential",
  beforeEach: () => {
    fakeCamilleDocuments();
    fakeRuns({
      consent: CONSENT_GIVEN,
      estimate: {
        ...camilleEstimate(),
        can_start: false,
        blockers: [
          {
            code: "no_credential",
            reason: "Add a model credential before a run can start.",
          },
        ],
      },
    });
  },
};

/** What is read, the tokens, the model and the limits, unfolded. */
export const WhatIsRead: Story = {
  name: "What is read, and the limits",
  play: unfold,
};

/** A document read in an earlier run is reused, unless Read again is ticked. */
export const ReusedFromAnEarlierRun: Story = {
  name: "Reused from an earlier run, read again",
  beforeEach: () => {
    fakeCamilleDocuments();
    const estimate = camilleEstimate();
    fakeRuns({
      consent: CONSENT_GIVEN,
      estimate: {
        ...estimate,
        documents: estimate.documents.map((one, index) =>
          index === 0 ? { ...one, reused_steps: 1 } : one,
        ),
        reused_steps: 1,
        unreadable: [
          {
            id: "doc-secret",
            name: "server-notes.txt",
            code: "secret",
            reason:
              "Its text may hold a password or a key. Check it under Documents first.",
          },
        ],
      },
    });
  },
  play: unfold,
};

/** While the estimate is computed, the button waits with a spinner. */
export const ComputingTheEstimate: Story = {
  name: "Computing the estimate",
  beforeEach: () => {
    server.use(
      http.post("/api/ingestion/runs/estimate", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
};

/** The estimate could not be computed: why, and the button stays off. */
export const EstimateFailed: Story = {
  name: "The estimate could not be computed",
  beforeEach: () => {
    server.use(
      http.post("/api/ingestion/runs/estimate", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** The click is on its way: consent recorded, then the start. */
export const Starting: Story = {
  name: "Starting the run",
  beforeEach: () => {
    server.use(
      http.post("/api/ingestion/runs", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
  play: async (context) => {
    await tick(context);
    await start(context);
  },
};

/** The figures changed since they were read: nothing started, the new ones wait for another click. */
export const TheEstimateChanged: Story = {
  name: "The estimate changed, nothing started",
  beforeEach: () => {
    fakeCamilleDocuments();
    const changed = {
      ...camilleEstimate(),
      tokens: { low: 82_000, high: 230_500, ceiling: 461_000 },
      quote_id: QUOTE_B,
      consent: { version: 1, accepted: true },
    };
    fakeRuns({
      consent: CONSENT_GIVEN,
      estimate: camilleEstimate(),
      startAnswers: [
        () =>
          HttpResponse.json(
            {
              detail: "The estimate has changed.",
              code: "estimate_changed",
              estimate: changed,
            },
            { status: 409 },
          ),
      ],
    });
  },
  play: start,
};

/** On a phone the button takes the width and its words wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  play: tick,
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
