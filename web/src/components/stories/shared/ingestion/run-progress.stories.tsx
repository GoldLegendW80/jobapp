import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { RunProgress } from "@/components/shared/ingestion/run-progress";
import { Card, CardContent } from "@/components/ui/card";
import {
  doneRun,
  pausedRun,
  runningRun,
} from "@/design-system/fixtures/camille-ingestion";
import {
  cancelledRun,
  closingRun,
  everyDocumentState,
  failedRun,
  fakeShownRuns,
  partialRun,
  pausedOnFailureRun,
  stoppingRun,
  waitingRun,
} from "@/design-system/fixtures/shared-ingestion";
import { withApp } from "@/design-system/with-app";
import { fakeDocuments } from "@/test/ingestion";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Ingestion/Run progress",
  component: RunProgress,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The run of the agent that reads the person's documents, while it works and once it has ended: its state, the one action that leaves it, its figures and every step. In the Review step of Fill my profile and in the welcome wizard.",
      },
    },
  },
  beforeEach: () => {
    fakeShownRuns([runningRun()]);
  },
} satisfies Meta<{ flat?: boolean }>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The CV read, the notes being read: which document, what was found, the tokens, and Cancel. */
export const FollowARunAtWork: Story = {
  name: "Follow a run at work",
};

/** The person's other agent runs use every place: the run starts by itself when one ends. */
export const WaitingForItsTurn: Story = {
  name: "Waiting for its turn",
  beforeEach: () => {
    fakeShownRuns([waitingRun()]);
  },
};

/** Cancel was clicked: the run ends after the step in progress. */
export const Stopping: Story = {
  name: "Stopping after the step in progress",
  beforeEach: () => {
    fakeShownRuns([stoppingRun()]);
  },
};

/** At its token limit, the run asks before it spends more: Continue names the tokens it adds. */
export const AskedBeforeSpendingMore: Story = {
  name: "Asked before spending more tokens",
  beforeEach: () => {
    fakeShownRuns([pausedRun()]);
  },
};

/** The figure changed before the click: nothing was added, and the new figure waits for another click. */
export const TheFigureChanged: Story = {
  name: "The figure changed before Continue",
  beforeEach: () => {
    const paused = pausedRun();
    const now = {
      ...paused,
      tokens: {
        ...paused.tokens,
        continue_adds: 80_000,
        continue_ceiling: paused.tokens.ceiling + 80_000,
      },
    };
    let refused = false;
    fakeShownRuns([paused]);
    server.use(
      http.post("/api/ingestion/runs/:id/continue", () => {
        refused = true;
        return HttpResponse.json(
          {
            detail: "What a Continue adds has changed.",
            code: "continue_changed",
            continue_adds: 80_000,
            run: now,
          },
          { status: 409 },
        );
      }),
      http.get("/api/ingestion/runs/:id", () =>
        HttpResponse.json(refused ? now : paused),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: /^Continue with/,
      }),
    );
  },
};

/** At its limit with nothing left to read: no question, the run closes by itself. */
export const ClosingByItself: Story = {
  name: "At its limit with nothing left to read",
  beforeEach: () => {
    fakeShownRuns([closingRun()]);
  },
};

/** A failure paused the run: why, and Resume starts the step again. */
export const PausedByAFailure: Story = {
  name: "Paused by a failure",
  beforeEach: () => {
    fakeShownRuns([pausedOnFailureRun()]);
  },
};

/** Everything read: what was found and how many proposals wait for review. */
export const Finished: Story = {
  name: "Finished, everything read",
  beforeEach: () => {
    fakeShownRuns([doneRun()]);
  },
};

/** Finished on an API key, with documents not read, each with its reason, and the cost. */
export const FinishedNotEverythingRead: Story = {
  name: "Finished, not everything read",
  beforeEach: () => {
    fakeShownRuns([partialRun()]);
    fakeDocuments(everyDocumentState());
  },
};

/** Every step of this run, opened: one line per document read, a failed one in red. */
export const EveryStepOpened: Story = {
  name: "Every step opened",
  beforeEach: () => {
    fakeShownRuns([partialRun()]);
    fakeDocuments(everyDocumentState());
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Every step of this run",
      }),
    );
  },
};

/** The engine closed the run: its sentence says what was kept. */
export const Failed: Story = {
  name: "The run failed",
  beforeEach: () => {
    fakeShownRuns([failedRun()]);
  },
};

/** Cancelled before it read anything: nothing was added to the profile. */
export const Cancelled: Story = {
  name: "The run was cancelled",
  beforeEach: () => {
    fakeShownRuns([cancelledRun()]);
  },
};

/** The list of runs answered, the run itself not yet. */
export const Loading: Story = {
  name: "Loading the run",
  beforeEach: () => {
    server.use(
      http.get("*/api/ingestion/runs/:id", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
};

/** The run could not be loaded: what happened, and Retry. */
export const CouldNotLoad: Story = {
  name: "The run could not be loaded",
  beforeEach: () => {
    server.use(
      http.get("*/api/ingestion/runs/:id", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** Inside a card already (the welcome wizard): no surface of its own. */
export const InsideACard: Story = {
  name: "Inside a card",
  args: { flat: true },
  decorators: [
    (Story) => (
      <Card>
        <CardContent>
          <Story />
        </CardContent>
      </Card>
    ),
  ],
};

/** On a phone the figures go in one column and the buttons wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  beforeEach: () => {
    fakeShownRuns([pausedRun()]);
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
