import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { AGENT_FLOWS } from "@/design-system/fixtures/agent-flows";
import { withApp } from "@/design-system/with-app";

import { WorkflowDiagram, type NodeRun } from "../components/workflow-diagram";

const meta = {
  title: "Pages/Admin/Workflow diagram",
  component: WorkflowDiagram,
  decorators: [withApp],
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Above each category's table on Admin, Agents, drawn as a run graph: the workflow's triggers on its \"on:\" line, then its steps as cards in columns in the order things happen, joined by connectors. An agent's card opens its page and its tooltip names the agent and what starts it; agents that run side by side are one card that lists them; a dashed connector goes back to an earlier step. Fit, minus and plus in the corner; drag or the arrow keys to move. A run can give each step a status, a duration and a link.",
      },
    },
  },
  args: { title: "Finding jobs", flow: AGENT_FLOWS.finding },
} satisfies Meta<typeof WorkflowDiagram>;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an administrator, I want to see what starts a sweep and which agents it runs, so that I understand how postings are found and read. */
export const FindingJobs: Story = {};

/** As an administrator, I want to see how an Apply or a Like reaches the apply agents and the person's approval, so that I know who fills, answers and checks. */
export const Applying: Story = {
  args: { title: "Applying", flow: AGENT_FLOWS.applying },
};

/** As an administrator, I want to see the writers, the hiring panel and its rebuild, and the chats, so that I understand how a CV and a letter are made. */
export const Documents: Story = {
  args: { title: "Documents", flow: AGENT_FLOWS.documents },
};

/** As an administrator, I want to open the hiring panel's card and see its seven reviewers, so that one card stands for the agents that run side by side. */
export const HiringPanelOpened: Story = {
  args: { title: "Documents", flow: AGENT_FLOWS.documents },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /Hiring panel/ }),
    );
  },
};

/** As an administrator, I want to see how documents, the About you parts and the job titles reach the profile, so that I know which agent proposes what. */
export const ProfileAndImport: Story = {
  args: { title: "Profile and import", flow: AGENT_FLOWS.profile },
};

/** As an administrator, I want to see the three agents of a posting's page side by side, so that I know none of them starts another. */
export const JobsAndChat: Story = {
  args: { title: "Jobs and chat", flow: AGENT_FLOWS.jobs },
};

const done = (duration: string): NodeRun => ({ status: "success", duration });

/** As an administrator, I want a finished run drawn with each step's status and time, the failed one marked, so that I see at once where it stopped. */
export const AFinishedRunWithOneFailedStep: Story = {
  args: {
    title: "Documents",
    flow: AGENT_FLOWS.documents,
    runs: {
      click: done("0s"),
      documents_cv: done("1m 48s"),
      panel: done("4m 02s"),
      documents_panel_screen: done("21s"),
      documents_panel_manager: done("34s"),
      documents_panel_reader_manager: done("29s"),
      documents_panel_reader_rival: { status: "failure", duration: "38s" },
      documents_panel_reader_ceo: done("31s"),
      documents_panel_reader_recruiter: done("27s"),
      documents_panel_reader_teammate: done("33s"),
      documents_panel_merge: { status: "skipped" },
      documents_letter: { status: "cancelled" },
    },
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: /Hiring panel/ }),
    );
  },
};

/** As an administrator watching a run, I want the steps still waiting or going shown as such, so that I know it is not finished. */
export const ARunUnderWay: Story = {
  args: {
    title: "Finding jobs",
    flow: AGENT_FLOWS.finding,
    runs: {
      search: done("0s"),
      sweep: { status: "running", duration: "6m 10s" },
      browser_discovery: { status: "running", duration: "5m 52s" },
      posting_reader: { status: "queued" },
    },
  },
};

/** As an administrator working at night, I want the drawing as readable in the dark theme, so that nothing disappears. */
export const InTheDark: Story = {
  args: { title: "Applying", flow: AGENT_FLOWS.applying },
  globals: { theme: "Dark" },
};

/** As an administrator on my phone, I want the whole drawing fitted inside its frame, to zoom and drag it there, so that the page itself never scrolls sideways. */
export const OnAPhone: Story = {
  args: { title: "Documents", flow: AGENT_FLOWS.documents },
  globals: { viewport: { value: "mobile2", isRotated: false } },
};
