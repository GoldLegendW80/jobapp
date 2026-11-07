import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { expect, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import {
  AGENT_PAGE,
  AGENT_PAGE_CHANGED,
  AGENTS,
  agentList,
} from "@/design-system/fixtures/admin-agents";
import type { AgentPage } from "@/features/admin/api/agents-api";
import { admin, fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

/** GET /api/admin/agents/:name answers this page (404 for another name), a server error, or never. */
function fakeAgent(page: AgentPage | "error" | "wait") {
  server.use(
    http.get("*/api/admin/agents", () => HttpResponse.json(agentList(AGENTS))),
    http.get("*/api/admin/agents/:name", async ({ params }) => {
      if (page === "wait") await delay("infinite");
      if (page === "error" || page === "wait")
        return HttpResponse.json({ detail: "Server error" }, { status: 500 });
      if (params.name !== page.name)
        return HttpResponse.json(
          { detail: "No agent has this name." },
          { status: 404 },
        );
      return HttpResponse.json(page);
    }),
  );
}

const meta = {
  title: "Pages/Admin/Agent",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "One agent: everything that makes it, its recent runs and versions, and the fields an administrator can change for its next runs. Route /admin/agents/:name, for administrators only.",
      },
    },
  },
  render: () => <AppAt path="/admin/agents/browser_discovery" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an administrator, I want to see everything that makes an agent, its tools, limits and recent runs, so that I understand what it does before I change it. */
export const ReviewAnAgent: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgent(AGENT_PAGE);
  },
};

/** As an administrator, I want to be told what the code changed since the version that runs was saved, with the prompt's diff, so that I can keep my version or go back to the code's. */
export const SeeWhatTheCodeChanged: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgent(AGENT_PAGE_CHANGED);
  },
};

/** As an administrator, I want a field I cannot save with marked next to it, so that I fix it before saving a version the runs would fail on. */
export const FixAFieldBeforeSaving: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgent(AGENT_PAGE);
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.clear(await canvas.findByLabelText("Description"));
    const wall = canvas.getByLabelText("Wall time, in seconds");
    await userEvent.clear(wall);
    await userEvent.type(wall, "7200");
    await expect(
      canvas.getByText("The description cannot be empty."),
    ).toBeVisible();
    await expect(
      canvas.getByRole("button", { name: "Save as a new version" }),
    ).toBeDisabled();
  },
};

/** As an administrator who follows an old link, I want to be told no agent has this name, so that I go back to the list instead of waiting. */
export const UnknownAgent: Story = {
  render: () => <AppAt path="/admin/agents/old_finder" />,
  beforeEach: () => {
    fakeSession(admin);
    fakeAgent(AGENT_PAGE);
  },
};

/** As an administrator, I want to know when the agent could not be loaded and try again, so that I am never left with a blank page. */
export const AgentUnavailable: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgent("error");
  },
};

/** As an administrator, I want to see that the agent is on its way, and to be told when the server has not answered within five seconds, so that I wait or retry instead of reloading. */
export const LoadingTheAgent: Story = {
  beforeEach: () => {
    fakeSession(admin);
    fakeAgent("wait");
  },
};

/** As an invited friend who types the admin address, I want the page not found, so that I am told plainly this is not for me. */
export const MemberSeesNotFound: Story = {
  beforeEach: () => {
    fakeSession(member);
    fakeAgent(AGENT_PAGE);
  },
};

/** As an administrator whose session ended, I want to land on the sign in page, so that I can sign in and come back here. */
export const SignedOutGoesToSignIn: Story = {
  beforeEach: () => {
    fakeSession(null);
  },
};

/** As an administrator on my phone, I want the agent's fields and tables to fit the screen, so that I can check an agent away from my desk. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeSession(admin);
    fakeAgent(AGENT_PAGE_CHANGED);
  },
};
