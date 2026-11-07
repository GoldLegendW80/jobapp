import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { delay } from "msw";
import { fn, userEvent, within } from "storybook/test";

import { FieldsList } from "@/components/shared/apply/fields-list";
import type { Answer } from "@/features/apply/api/apply-api";
import { FILLED, ONE_LEFT } from "@/design-system/fixtures/applications";
import {
  fakeRemember,
  OPTIONAL_LEFT,
} from "@/design-system/fixtures/shared-apply";
import { APP_ID, fakeAnswer, leftAnswer } from "@/test/apply";

const LEFT: Answer[] = [
  ...FILLED.slice(0, 4),
  leftAnswer("salary", "Salary expectations", {
    required: true,
    remember: "on",
  }),
  leftAnswer("why_us", "Why do you want to join Lumen Freight?", {
    kind: "textarea",
    required: true,
    remember: "off",
  }),
  leftAnswer("heard", "How did you hear about us?", {
    kind: "select",
    options: ["Company website", "A friend or colleague", "A job board"],
  }),
  leftAnswer("portfolio", "Portfolio", { kind: "file", required: true }),
  leftAnswer("skipped:widget-3", "Pick your available interview slots"),
];

const LETTER_LEFT: Answer[] = [
  ...FILLED.slice(0, 3),
  leftAnswer("cover_letter_text", "Cover letter", {
    kind: "textarea",
    required: true,
    max_chars: 10000,
    left_reason: "letter_too_long",
  }),
];

const LONG: Answer[] = [
  ...FILLED,
  fakeAnswer(
    "pipeline",
    "Describe a data pipeline you built and still run, and what you would change today if you started it again",
    "At Northwind Rail I built the pipeline that turns ticket scans into hourly ridership figures: Kafka topics per station, a dbt project of about ninety models, and Airflow for the nightly rebuild. It has run for three years with two incidents, both from upstream schema changes.\n\nToday I would add data contracts at the producer side, so a renamed field fails the producer's build instead of our morning dashboard.",
    { kind: "textarea", source: "agent" },
  ),
  fakeAnswer(
    "website",
    "Personal website",
    "https://zoe-angstrom.example.org/projects/streaming-ridership-pipeline-northwind-rail",
    { source: "identity" },
  ),
];

const meta = {
  title: "Shared/Apply/Fields list",
  component: FieldsList,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The fields of a filled form, one row each: label, value and where it came from. A field left for the person sits in an amber frame with its input open, a banner counts them, and any other answer can be corrected with Edit. In Your application and on the review queue's cards.",
      },
    },
  },
  args: {
    answers: FILLED,
    editable: true,
    saving: false,
    onSave: fn(() => Promise.resolve(true)),
    onDirtyChange: fn(),
    applicationId: APP_ID,
  },
  // A saved answer becomes the person's, as the api's answer does on the page.
  render: function Render(args) {
    const [answers, setAnswers] = useState(args.answers);
    const [saving, setSaving] = useState(false);
    return (
      <FieldsList
        {...args}
        answers={answers}
        saving={args.saving || saving}
        onSave={async (answer, value) => {
          setSaving(true);
          await args.onSave(answer, value);
          await delay(400);
          setAnswers((old) =>
            old.map((one) =>
              one.field_key === answer.field_key
                ? { ...one, value, source: "person", you_answer: false }
                : one,
            ),
          );
          setSaving(false);
          return true;
        }}
      />
    );
  },
  beforeEach: () => {
    fakeRemember("saved");
  },
} satisfies Meta<typeof FieldsList>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every field filled from the profile, documents and saved answers; each can be corrected with Edit. */
export const AllFilled: Story = {
  name: "Every field filled",
};

/** Edit opens one answer; Done closes it. */
export const CorrectAnAnswer: Story = {
  name: "Correct an answer",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Edit: Notice period",
      }),
    );
  },
};

/** One required question left: an amber banner names it and its row waits for the answer. */
export const OneLeftForYou: Story = {
  name: "One question left for you",
  args: { answers: ONE_LEFT },
};

/** Several left, of every kind: typed here, picked from the form's list, or answered on the site. */
export const SeveralLeftForYou: Story = {
  name: "Several questions left, of every kind",
  args: { answers: LEFT },
};

/** Optional ones left: they are sent empty if not answered. */
export const OptionalLeft: Story = {
  name: "Optional questions left",
  args: { answers: OPTIONAL_LEFT },
};

/** Answered and remembered: the row says the next form with this question is filled with it. */
export const RememberedForNextForms: Story = {
  name: "Answered and remembered for next forms",
  args: { answers: LEFT },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      await canvas.findByLabelText("Answer for Salary expectations"),
      "55000 EUR a year",
    );
    const [save] = canvas.getAllByRole("button", { name: "Save answer" });
    if (save) await userEvent.click(save);
  },
};

/** Saved answers already hold one: it was kept. */
export const RememberKept: Story = {
  name: "A saved answer was kept",
  args: { answers: LEFT },
  beforeEach: () => {
    fakeRemember("kept");
  },
  play: RememberedForNextForms.play,
};

/** Saved on this form, but not for next forms: the row says why. */
export const RememberFails: Story = {
  name: "Saved on this form only",
  args: { answers: LEFT },
  beforeEach: () => {
    fakeRemember("error");
  },
  play: RememberedForNextForms.play,
};

/** The letter was too long for the form's box: the assistant left it, and the row says why. */
export const LetterTooLong: Story = {
  name: "The letter was too long",
  args: { answers: LETTER_LEFT },
};

/** After the approval, the list is a record: no input, no Edit. */
export const ReadOnly: Story = {
  name: "Read only, after the approval",
  args: { answers: ONE_LEFT, editable: false },
};

/** Long labels and values wrap; a long address breaks anywhere. */
export const LongContent: Story = {
  name: "Long questions and answers",
  args: { answers: LONG },
};

/** On a phone, one column: label, value, source. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
  args: { answers: ONE_LEFT },
};
