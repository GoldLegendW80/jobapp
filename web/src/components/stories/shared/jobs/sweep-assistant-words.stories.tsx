import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { SweepAssistantWords } from "@/components/shared/jobs/sweep-assistant-words";
import {
  assistantWordsHandlers,
  WEBSITE_RUN_ID,
  WORDS_SWEEP_ID,
} from "@/design-system/fixtures/assistant-words";
import { withApp } from "@/design-system/with-app";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Jobs/What the assistants said",
  component: SweepAssistantWords,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "In the summary of a search: each assistant that searched a website or the whole web, named as Activity names it, and one click shows what it said when it ended, in its own words, as plain text.",
      },
    },
  },
  args: { sweepId: WORDS_SWEEP_ID },
  beforeEach: () => {
    server.use(...assistantWordsHandlers);
  },
} satisfies Meta<typeof SweepAssistantWords>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Two assistants, folded: one click each. */
export const Folded: Story = { name: "Two assistants" };

/** The website search's words, opened; the web search's link and markup stay plain text. */
export const Opened: Story = {
  name: "Its words, opened",
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      await canvas.findByRole("button", { name: /^Website search, / }),
    );
    await userEvent.click(
      await canvas.findByRole("button", { name: /^Web search, / }),
    );
  },
};

/** A run whose words could not be read: the way to Activity. */
export const NotLoaded: Story = {
  name: "Words not loaded",
  beforeEach: () => {
    server.use(
      http.get(`*/api/agents/runs/${WEBSITE_RUN_ID}`, () =>
        HttpResponse.json({ detail: "Not found" }, { status: 404 }),
      ),
    );
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: /^Website search, /,
      }),
    );
  },
};
