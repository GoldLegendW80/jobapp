import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse, sse } from "msw";

import { AppAt } from "@/design-system/app-at";
import {
  fakeRunsApi,
  KESTREL,
  makeRun,
  RUN_ID,
  RUNNING_STEPS,
} from "@/design-system/fixtures/runs";
import { fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Activity/Run",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "One run of the assistant, live: its steps as they happen, the model, tokens, amount and duration, and Cancel while it works. Route /runs/:id, from a row of Assistant activity.",
      },
    },
  },
  render: () => <AppAt path={`/runs/${RUN_ID}`} />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As a job seeker, I want to read every step of a finished run and what it cost, so that I understand how the assistant reached its answer. */
export const ReadAFinishedRun: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi({ detail: makeRun({ job_id: KESTREL.id, job: KESTREL }) });
  },
};

/** As a job seeker, I want to follow a run while it works and stop it if I need to, so that I am never waiting on something I cannot see. */
export const FollowARunAtWork: Story = {
  beforeEach: () => {
    fakeSession(member);
    const started = new Date(Date.now() - 25_000).toISOString();
    fakeRunsApi({
      detail: makeRun({
        status: "running",
        input_tokens: null,
        output_tokens: null,
        cost_usd: null,
        started_at: started,
        finished_at: null,
        duration_s: null,
      }),
      steps: RUNNING_STEPS,
      open: true,
    });
  },
};

/** As a job seeker, I want a failed run to say why it stopped, so that I know whether to start it again. */
export const SeeWhyARunFailed: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi({
      detail: makeRun({
        agent: "apply_form",
        status: "failed",
        error: "max_turns",
        credential_kind: "anthropic_api_key",
      }),
      steps: [
        ...RUNNING_STEPS,
        {
          seq: 7,
          at: "2026-11-05T08:14:20Z",
          kind: "status",
          data: { status: "failed", error: "max_turns" },
        },
      ],
    });
  },
};

/** As a job seeker, I want to see that the run is loading, so that I wait for its steps. */
export const RunLoading: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi();
    server.use(
      http.get("*/api/agents/runs/:id", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
};

/** As a job seeker following an old link, I want to be told the run no longer exists, so that I go back to the list instead of waiting. */
export const RunNoLongerExists: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi();
    server.use(
      http.get("*/api/agents/runs/:id", () =>
        HttpResponse.json({ detail: "Run not found." }, { status: 404 }),
      ),
      sse<{ gone: object }>("*/api/agents/runs/:id/stream", ({ client }) => {
        client.send({ event: "gone", data: {} });
      }),
    );
  },
};

/** As a job seeker, I want to know when the run could not be loaded, so that a server error does not look like an empty run. */
export const RunUnavailable: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi({ steps: [], open: true });
    server.use(
      http.get("*/api/agents/runs/:id", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
  },
};

/** As a visitor who is not signed in, I want to be sent to sign in first and come back to this run, so that a shared link still works once I am in. */
export const SignedOut: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As a job seeker on my phone, I want the steps and totals to fit the screen, so that I can follow a run on the go. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(member);
    fakeRunsApi({ detail: makeRun({ job_id: KESTREL.id, job: KESTREL }) });
  },
};
