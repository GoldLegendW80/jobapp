import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  CAMILLE_REFUSED,
  camilleDocuments,
  camilleEstimate,
  doneRun,
  fakeCamilleDocuments,
  fakeCamilleProposals,
  pausedRun,
  runningRun,
} from "@/design-system/fixtures/camille-ingestion";
import {
  camille,
  camilleProfile,
} from "@/design-system/fixtures/camille-profile";
import { fakeSession } from "@/test/auth";
import {
  fakeDocument,
  fakeDocuments,
  fakePrompt,
  PROMPT,
} from "@/test/ingestion";
import { fakeRuns } from "@/test/ingestion-runs";
import type { Run } from "@/features/ingestion/api/runs-api";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Profile/Fill my profile",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "Fill my profile with an agent: analyze your computer, add documents, then review what the agent proposes, one step open at a time. Route /profile/fill, the open step in `?step=`.",
      },
    },
  },
  render: () => <AppAt path="/profile/fill" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** The prompt to copy, written for Camille. */
const CAMILLE_PROMPT = {
  ...PROMPT,
  claude_code: PROMPT.claude_code.replace("Zoë Ångström", "Camille Martin"),
  codex: PROMPT.codex.replace("Zoë Ångström", "Camille Martin"),
};

/** Camille signed in, her profile, her documents and the given runs; the consent given. */
function camilleFilling(runs: Run[] = []) {
  fakeSession(camille);
  fakeProfile({
    identity: { ...camilleProfile().identity, headline: null, city: null },
  });
  fakePrompt(CAMILLE_PROMPT);
  fakeCamilleDocuments();
  fakeRuns({
    runs,
    estimate: camilleEstimate(),
    consent: {
      version: 1,
      text: "When you start a run, the app sends the text of the documents you selected to Anthropic.",
      reminder:
        "The selected documents are sent to Anthropic through your own credential.",
      accepted: true,
      accepted_at: "2026-11-04T08:20:00Z",
    },
  });
  fakeCamilleProposals();
}

/** As a new user with nothing to add yet, I want a prompt for my coding assistant to analyze my computer for my recruiter profile, so that I start from more than a blank profile. */
export const AnalyzeMyComputerWithACodingAssistant: Story = {
  beforeEach: () => {
    fakeSession(camille);
    fakeProfile(null);
    fakePrompt(CAMILLE_PROMPT);
    fakeDocuments([]);
    fakeRuns();
  },
};

/** As a job seeker, I want to add my CV and notes and see each one read, so that the agent works from what I already have. */
export const AddMyDocuments: Story = {
  render: () => <AppAt path="/profile/fill?step=documents" />,
  beforeEach: () => {
    camilleFilling();
  },
};

/** As a job seeker, I want to know when a file cannot be read and why, so that I add it in a form the app reads. */
export const SeeWhyAFileWasRefused: Story = {
  render: () => <AppAt path="/profile/fill?step=documents" />,
  beforeEach: () => {
    camilleFilling();
    fakeCamilleDocuments([
      fakeDocument({
        id: "doc-scan",
        name: "diploma-scan.pdf",
        kind: "pdf",
        mime: "application/pdf",
        size: 912_384,
        status: "failed",
        pages: 1,
        pages_text: 0,
        failed_reason:
          "No text could be read from this scan. Add a PDF with selectable text, or a photo of each page.",
        created_at: "2026-11-04T08:16:00Z",
      }),
      ...camilleDocuments(),
    ]);
  },
};

/** As a job seeker, I want to see what a run will read and cost before it starts, so that I start it knowingly. */
export const StartARun: Story = {
  render: () => <AppAt path="/profile/fill?step=review" />,
  beforeEach: () => {
    camilleFilling();
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(
      await page.findByRole("button", { name: "Select all" }),
    );
  },
};

/** As a job seeker, I want to follow a run while it reads my documents, so that I know how far it is and what it has spent. */
export const FollowARunInProgress: Story = {
  render: () => <AppAt path="/profile/fill?step=review" />,
  beforeEach: () => {
    camilleFilling([runningRun()]);
  },
};

/** As a job seeker, I want a run that reached its ceiling to ask before it spends more, so that I keep control of what it costs. */
export const DecideOnAPausedRun: Story = {
  render: () => <AppAt path="/profile/fill?step=review" />,
  beforeEach: () => {
    camilleFilling([pausedRun()]);
  },
};

/** As a job seeker, I want to accept or reject what the agent proposes, each with the quote it comes from, so that nothing reaches my profile without my decision. */
export const ReviewWhatTheAgentProposes: Story = {
  render: () => <AppAt path="/profile/fill?step=review" />,
  beforeEach: () => {
    camilleFilling([doneRun()]);
  },
};

/** As a job seeker whose CV lists more skills than the review shows, I want to read why some proposals were not kept, so that I know what to add by hand. */
export const SeeWhyProposalsWereNotKept: Story = {
  render: () => <AppAt path="/profile/fill?step=review" />,
  beforeEach: () => {
    camilleFilling([{ ...doneRun(), refused: CAMILLE_REFUSED }]);
    fakeCamilleProposals(undefined, CAMILLE_REFUSED);
  },
};

/** As a job seeker, I want to know when my documents could not be loaded, so that I can try again instead of adding them twice. */
export const DocumentsUnavailable: Story = {
  render: () => <AppAt path="/profile/fill?step=documents" />,
  beforeEach: () => {
    camilleFilling();
    server.use(
      http.get("*/api/ingestion/documents", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** As a job seeker, I want to review proposals on my phone, so that I can fill my profile away from my desk. */
export const ReviewOnMyPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  render: () => <AppAt path="/profile/fill?step=review" />,
  beforeEach: () => {
    camilleFilling([doneRun()]);
  },
};
