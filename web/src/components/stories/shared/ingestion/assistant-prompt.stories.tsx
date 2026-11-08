import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";

import { AssistantPrompt } from "@/components/shared/ingestion/assistant-prompt";
import { withApp } from "@/design-system/with-app";
import { fakePrompt } from "@/test/ingestion";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Ingestion/Assistant prompt",
  component: AssistantPrompt,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The prompt a person copies and runs with Claude Code or Codex on their own computer, which writes one file about their work: Prepare in Fill my profile, and step 1 of the welcome wizard. `addIt` ends the last instruction with where that page takes the file.",
      },
    },
  },
  args: {
    addIt:
      "open Other documents below, paste its text or add the file, and read it into your profile",
  },
  beforeEach: () => {
    fakePrompt();
  },
} satisfies Meta<typeof AssistantPrompt>;

export default meta;
type Story = StoryObj<typeof meta>;

/** As in the welcome wizard: the file is pasted on the same page. */
export const InTheWizard: Story = { name: "In the welcome wizard" };

/** As in Fill my profile: the file is added in the next step. */
export const InFillMyProfile: Story = {
  name: "In Fill my profile",
  args: { addIt: "add it in step 2" },
};

export const Loading: Story = {
  beforeEach: () => {
    server.use(
      http.get("*/api/ingestion/prompt", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
};

export const LoadFailed: Story = {
  name: "Load failed",
  beforeEach: () => {
    server.use(
      http.get("*/api/ingestion/prompt", () =>
        HttpResponse.json({ detail: "unavailable" }, { status: 503 }),
      ),
    );
  },
};
