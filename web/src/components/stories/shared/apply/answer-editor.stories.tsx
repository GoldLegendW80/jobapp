import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { fn, userEvent, within } from "storybook/test";

import { AnswerEditor } from "@/components/shared/apply/answer-editor";
import type { Answer } from "@/features/apply/api/apply-api";
import { fakeAnswer, leftAnswer } from "@/test/apply";

const LETTER = `Dear hiring team,

I would like to join Lumen Freight as a senior data engineer. For three years I have run the pipeline that turns ticket scans into hourly ridership figures at Northwind Rail, and I would bring the same care to your shipment data.

Kind regards,
Zoë Ångström`;

const meta = {
  title: "Shared/Apply/Answer editor",
  component: AnswerEditor,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Type or correct one answer of a filled form before approval: Yes and No buttons, the field's own choices, or a text box with its character count and Save answer. In the fields list of Your application and of the review queue's cards.",
      },
    },
  },
  args: {
    answer: fakeAnswer("notice", "Notice period", "One month", {
      source: "bank",
    }),
    saving: false,
    onSave: fn(() => Promise.resolve(true)),
    onDirtyChange: fn(),
    remember: null,
  },
  // A saved answer becomes the field's value, as the api's answer does on the page.
  render: function Render(args) {
    const [answer, setAnswer] = useState<Answer>(args.answer);
    return (
      <div className="max-w-xl">
        <AnswerEditor
          {...args}
          key={JSON.stringify(answer.value)}
          answer={answer}
          onSave={async (value, remember) => {
            await args.onSave(value, remember);
            setAnswer({
              ...answer,
              value,
              source: "person",
              you_answer: false,
            });
            return true;
          }}
        />
      </div>
    );
  },
} satisfies Meta<typeof AnswerEditor>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A short answer from Saved answers, corrected in a text box. */
export const ShortText: Story = {
  name: "Correct a short answer",
};

/** Typed and not saved: Save answer can be clicked, and the page keeps Approve waiting meanwhile. */
export const UnsavedChange: Story = {
  name: "A change not saved yet",
  play: async ({ canvasElement }) => {
    const box = await within(canvasElement).findByLabelText(
      "Answer for Notice period",
    );
    await userEvent.clear(box);
    await userEvent.type(box, "Two months");
  },
};

/** A question left for the person, empty: Save waits for an answer. */
export const LeftForYou: Story = {
  name: "A question left for you",
  args: {
    answer: leftAnswer("why_us", "Why do you want to join Lumen Freight?", {
      kind: "textarea",
      required: true,
    }),
  },
};

/** The box to keep the answer for next forms, offered ticked. */
export const RememberOfferedTicked: Story = {
  name: "Remember this answer, ticked",
  args: {
    answer: leftAnswer("salary", "Salary expectations", { required: true }),
    remember: "on",
  },
};

/** Offered unticked: the question names the company, so it is unlikely to come back as is. */
export const RememberOfferedUnticked: Story = {
  name: "Remember this answer, unticked",
  args: {
    answer: leftAnswer("why_us", "Why do you want to join Lumen Freight?", {
      kind: "textarea",
      required: true,
    }),
    remember: "off",
  },
};

/** A yes or no saves at once; the chosen one is black. */
export const YesOrNo: Story = {
  name: "Yes or no",
  args: {
    answer: fakeAnswer(
      "work_permit",
      "Are you allowed to work in France without a visa?",
      true,
      { kind: "yes_no", source: "answers", required: true },
    ),
  },
};

/** The field's own choices, saved as soon as one is picked. */
export const AChoice: Story = {
  name: "A choice from the form's list",
  args: {
    answer: fakeAnswer("heard", "How did you hear about us?", null, {
      kind: "select",
      source: "left",
      you_answer: true,
      options: [
        "Company website",
        "A friend or colleague",
        "A job board",
        "A recruiter",
      ],
    }),
  },
};

/** A long answer gets a text area. */
export const LongText: Story = {
  name: "A long answer",
  args: {
    answer: fakeAnswer(
      "pipeline",
      "Describe a data pipeline you built and still run",
      "At Northwind Rail I built the pipeline that turns ticket scans into hourly ridership figures: Kafka topics per station, a dbt project of about ninety models, and Airflow for the nightly rebuild.",
      { kind: "textarea", source: "agent" },
    ),
  },
};

/** The cover letter box: up to 10000 characters, its spacing and line breaks kept. */
export const CoverLetter: Story = {
  name: "The cover letter box",
  args: {
    answer: fakeAnswer("cover_letter_text", "Cover letter", LETTER, {
      kind: "textarea",
      source: "document",
      max_chars: 10000,
    }),
  },
};

/** A letter with a tab in it: the browser cannot type it, so it must go before saving. */
export const LetterWithAControlCharacter: Story = {
  name: "A letter with a tab in it",
  args: {
    answer: fakeAnswer(
      "cover_letter_text",
      "Cover letter",
      LETTER.replace("Dear hiring team,", "Dear hiring team,\t"),
      { kind: "textarea", source: "document", max_chars: 10000 },
    ),
  },
};

/** An answer longer than the form takes: it must be shortened before saving. */
export const TooLong: Story = {
  name: "Too long for the form",
  args: {
    answer: fakeAnswer(
      "summary",
      "Summary",
      "I build and run data pipelines for transport companies. ".repeat(20),
      { kind: "textarea", source: "agent", max_chars: 998 },
    ),
  },
};

/** While the answer is saved, the controls wait. */
export const Saving: Story = {
  name: "Saving",
  args: { saving: true },
};

/** On a phone, Save answer takes the whole width. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  args: RememberOfferedTicked.args,
};
