import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";

import { AppAt } from "@/design-system/app-at";
import { AGENTS, agentList } from "@/design-system/fixtures/admin-agents";
import type { AgentSummary } from "@/features/admin/api/agents-api";
import { admin, fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

/** GET /api/admin/agents answers these agents, a server error, or never. */
function fakeAgentList(agents: AgentSummary[] | "error" | "wait") {
  server.use(
    http.get("*/api/admin/agents", async () => {
      if (agents === "wait") await delay("infinite");
      if (agents === "error" || agents === "wait")
        return HttpResponse.json({ detail: "Server error" }, { status: 500 });
      return HttpResponse.json(agentList(agents));
    }),
  );
}

const meta = {
  title: "Pages/Admin/Agents",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "Every agent of the app grouped by category, what it runs on, its version and its last run. Route /admin/agents, for administrators only.",
      },
    },
  },
  render: () => <AppAt path="/admin/agents" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an administrator, I want every agent grouped by what it does, with its model, version and last run, so that I can see at a glance which one to open. */
export const BrowseAgentsByCategory: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgentList(AGENTS);
  },
};

/** As an administrator of an install without agents, I want the page to say so, so that an empty page is never mistaken for a broken one. */
export const NoAgentRegistered: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgentList([]);
  },
};

/** As an administrator, I want to know when the agents could not be loaded and try again, so that I am never left with a blank page. */
export const AgentsUnavailable: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgentList("error");
  },
};

/** As an administrator, I want to see that the agents are on their way, and to be told when the server has not answered within five seconds, so that I wait or retry instead of reloading. */
export const LoadingTheAgents: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgentList("wait");
  },
};

/** As an invited friend who types the admin address, I want the page not found, so that I am told plainly this is not for me. */
export const MemberSeesNotFound: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeAgentList(AGENTS);
  },
};

/** As an administrator whose session ended, I want to land on the sign in page, so that I can sign in and come back here. */
export const SignedOutGoesToSignIn: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As an administrator on my phone, I want each agent as a card of its own, so that I can read the list without scrolling sideways. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(admin);
    fakeAgentList(AGENTS);
  },
};
