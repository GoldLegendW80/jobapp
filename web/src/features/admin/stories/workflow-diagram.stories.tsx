import type { Meta, StoryObj } from "@storybook/react-vite";

import { AGENT_FLOWS } from "@/design-system/fixtures/agent-flows";
import { withApp } from "@/design-system/with-app";

import { WorkflowDiagram } from "../components/workflow-diagram";

const meta = {
  title: "Pages/Admin/Workflow diagram",
  component: WorkflowDiagram,
  decorators: [withApp],
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Above each category's table on Admin, Agents: the agents of that workflow and what starts or feeds each one, as the api lays them out. An agent opens its page; a dashed arrow goes back to an earlier step. On a narrow screen the drawing scrolls sideways inside its frame.",
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

/** As an administrator, I want to see how documents, the About you parts and the job titles reach the profile, so that I know which agent proposes what. */
export const ProfileAndImport: Story = {
  args: { title: "Profile and import", flow: AGENT_FLOWS.profile },
};

/** As an administrator, I want to see the three agents of a posting's page side by side, so that I know none of them starts another. */
export const JobsAndChat: Story = {
  args: { title: "Jobs and chat", flow: AGENT_FLOWS.jobs },
};

/** As an administrator working at night, I want the drawing as readable in the dark theme, so that nothing disappears. */
export const InTheDark: Story = {
  args: { title: "Applying", flow: AGENT_FLOWS.applying },
  globals: { theme: "Dark" },
};

/** As an administrator on my phone, I want the drawing to scroll sideways inside its frame, so that the page itself never does. */
export const OnAPhone: Story = {
  args: { title: "Documents", flow: AGENT_FLOWS.documents },
  globals: { viewport: { value: "mobile2", isRotated: false } },
};
