import type { Meta, StoryObj } from "@storybook/react-vite";

import { TimelineStep } from "@/components/shared/agents/run/timeline-step";
import { Trace } from "@/components/ui/trace";

const meta = {
  title: "Shared/Agents/Timeline step",
  component: TimelineStep,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One step of a run's timeline as a Trace line, in the run panel of a posting check and on the page of a run: its icon and title, a size beside it, a sentence, the answer or a tool's arguments under it, a tool's result that opens in place.",
      },
    },
  },
  decorators: [
    (Story) => (
      <Trace className="max-w-xl">
        <div className="flex min-w-0 flex-col gap-1">
          <Story />
        </div>
      </Trace>
    ),
  ],
  args: {
    view: {
      icon: "measure",
      title: "Started on claude-opus-5-5",
      body: { kind: "note", text: "Tools: get_job, get_profile" },
      tone: "muted",
    },
  },
} satisfies Meta<typeof TimelineStep>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A sentence about the step, in grey. */
export const Note: Story = { name: "A note" };

/** The answer as the model wrote it, line breaks kept. */
export const Answer: Story = {
  name: "An answer",
  args: {
    view: {
      icon: "answer",
      title: "Answer",
      body: {
        kind: "answer",
        text: "The posting fits well: SQL, dbt and Looker are all in your profile.\n\nHealthcare data standards are new to you, which the letter can address.",
      },
    },
  },
};

/** The answer while it is written: its title breathes. */
export const AnswerBeingWritten: Story = {
  name: "An answer being written",
  args: {
    writing: true,
    view: {
      icon: "answer",
      title: "Answer",
      body: { kind: "answer", text: "The posting fits well: SQL, dbt and" },
    },
  },
};

/** A tool call's arguments, each name with its value. */
export const Arguments: Story = {
  name: "Tool arguments",
  args: {
    view: {
      icon: "tool",
      title: "Tool call: get_job",
      body: {
        kind: "arguments",
        entries: [
          ["job_id", "8b1f6d53-2e4c-4a9f-a7b2-3d0e9f8a7b62"],
          ["section", "experience"],
        ],
      },
    },
  },
};

/** A private run shows sizes only, beside the title. */
export const Size: Story = {
  name: "A size, for a private run",
  args: {
    view: {
      icon: "succeeded",
      title: "Tool result",
      body: { kind: "size", text: "18,204 characters, 2 other blocks" },
    },
  },
};

/** A short result opens in place under its line. */
export const ShortResult: Story = {
  name: "A short result, folded",
  args: {
    view: {
      icon: "succeeded",
      title: "Tool result",
      body: {
        kind: "result",
        content: '{"title":"Analytics engineer","company":"Kestrel Health"}',
      },
    },
  },
};

/** A long result says its length beside the title. */
export const LongResult: Story = {
  name: "A long result, folded with its length",
  args: {
    view: {
      icon: "succeeded",
      title: "Tool result",
      body: {
        kind: "result",
        content: JSON.stringify(
          Array.from({ length: 30 }, (_, index) => ({
            company: `Invented company ${String(index + 1)}`,
            title: "Data engineer",
          })),
          null,
          2,
        ),
      },
    },
  },
};

/** A failed step is red, in words and colour. */
export const Failed: Story = {
  name: "A failed step",
  args: {
    view: {
      icon: "failed",
      title: "Failed",
      body: { kind: "note", text: "The run passed its limit of 8 turns." },
      tone: "error",
    },
  },
};

/** A refused tool is marked, its icon in the warning colour. */
export const Refused: Story = {
  name: "A refused tool",
  args: {
    view: {
      icon: "refused",
      title: "Refused: Bash",
      body: {
        kind: "note",
        text: "This tool is not available to this agent.",
      },
      tone: "warning",
    },
  },
};
