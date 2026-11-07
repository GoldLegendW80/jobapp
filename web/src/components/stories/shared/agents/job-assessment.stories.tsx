import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { expect, userEvent, waitFor, within } from "storybook/test";

import { JobAssessment } from "@/components/shared/agents/job-assessment";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import {
  FINISHED_STEPS,
  makeRun,
  RUNNING_STEPS,
} from "@/design-system/fixtures/runs";
import {
  DECIDED_PROPOSALS,
  fakeAssessment,
  fakeCredential,
  PENDING_PROPOSALS,
  TRIAGE_RUN,
} from "@/design-system/fixtures/shared-agents";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const FINISHED = { run: TRIAGE_RUN, steps: FINISHED_STEPS };

const meta = {
  title: "Shared/Agents/Job assessment",
  component: JobAssessment,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Agent assessment on a posting's page: Assess with agent starts the triage agent, its run shows live, and what it proposes (a note, a status) waits for Accept, Edit or Reject. Nothing changes the posting until the person accepts.",
      },
    },
  },
  args: { job: POSTING_DETAIL },
  beforeEach: () => {
    fakeCredential();
  },
} satisfies Meta<typeof JobAssessment>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Never assessed: click Assess with agent and watch it work, then decide on its proposals. */
export const ReadyToAssess: Story = {
  name: "Ready to assess",
  beforeEach: () => {
    fakeAssessment();
  },
};

/** No model credential: the button is off and the line says where to add one. */
export const NoModelCredential: Story = {
  name: "No model credential",
  beforeEach: () => {
    fakeAssessment();
    server.use(
      http.get("*/api/me/model-credential", () =>
        HttpResponse.json(
          { detail: "No model credential is saved." },
          { status: 404 },
        ),
      ),
    );
  },
};

/** The agent is at work: the button says Assessing and the run panel follows it. */
export const Assessing: Story = {
  name: "Assessing",
  beforeEach: () => {
    fakeAssessment({
      run: {
        run: makeRun({
          ...TRIAGE_RUN,
          status: "running",
          cost_usd: null,
          started_at: new Date(Date.now() - 12_000).toISOString(),
          finished_at: null,
          duration_s: null,
        }),
        steps: RUNNING_STEPS,
        open: true,
      },
    });
  },
};

/** A note and a status wait for the person: Accept, Edit the note, or Reject. */
export const ProposalsWaiting: Story = {
  name: "Proposals waiting for you",
  beforeEach: () => {
    fakeAssessment({ run: FINISHED, proposals: PENDING_PROPOSALS });
  },
};

/** Edit the note before accepting it. */
export const EditTheNote: Story = {
  name: "Edit the note, then accept",
  beforeEach: () => {
    fakeAssessment({ run: FINISHED, proposals: PENDING_PROPOSALS });
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Edit the note, then accept",
      }),
    );
  },
};

/** The proposals of the last check, decided: accepted, rejected, replaced. */
export const ProposalsDecided: Story = {
  name: "Proposals decided",
  beforeEach: () => {
    fakeAssessment({ run: FINISHED, proposals: DECIDED_PROPOSALS });
  },
};

/** The check ended without a proposal. */
export const ProposedNothing: Story = {
  name: "The assessment proposed nothing",
  beforeEach: () => {
    fakeAssessment({ run: FINISHED });
  },
};

/** The latest check is read: the button waits. */
export const Loading: Story = {
  name: "Loading",
  beforeEach: () => {
    fakeAssessment({ runsRead: "loading" });
  },
};

/** The start is refused: a toast says why. */
export const DidNotStart: Story = {
  name: "The assessment did not start",
  beforeEach: () => {
    fakeAssessment();
    server.use(
      http.post("*/api/agents/runs", () =>
        HttpResponse.json(
          {
            detail:
              "Your model credential was refused. Check it on the Account page.",
          },
          { status: 422 },
        ),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    const button = await within(canvasElement).findByRole("button", {
      name: "Assess with agent",
    });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
  },
};

/** On a phone the run and the proposals stack in one column. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  beforeEach: () => {
    fakeAssessment({ run: FINISHED, proposals: PENDING_PROPOSALS });
  },
};
