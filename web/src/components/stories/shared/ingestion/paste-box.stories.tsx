import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { PasteBox } from "@/components/shared/ingestion/paste-box";
import { withApp } from "@/design-system/with-app";
import { fakeDocuments, LIMITS } from "@/test/ingestion";
import { server } from "@/test/server";

const ABOUT =
  "Data engineer in Lyon. I build real time pipelines on Kafka and Flink, and I like turning a slow nightly batch into something people can watch live.";

const meta = {
  title: "Shared/Ingestion/Paste box",
  component: PasteBox,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Paste a text instead of a file, with the count the server enforces: beside the drop zone in the Documents step of Fill my profile and in the welcome wizard.",
      },
    },
  },
  args: { limits: LIMITS, onStored: () => undefined },
  beforeEach: () => {
    fakeDocuments([]);
  },
} satisfies Meta<typeof PasteBox>;

export default meta;
type Story = StoryObj<typeof meta>;

const paste =
  (text: string, submit: boolean) =>
  async ({ canvasElement }: { canvasElement: HTMLElement }) => {
    const box = within(canvasElement);
    await userEvent.click(await box.findByLabelText("Or paste a text"));
    await userEvent.paste(text);
    if (submit)
      await userEvent.click(box.getByRole("button", { name: "Add this text" }));
  };

/** Nothing pasted: Add this text waits for a text. */
export const NothingPastedYet: Story = {
  name: "Nothing pasted yet",
};

/** A text pasted, with its count, ready to add. */
export const TextPasted: Story = {
  name: "A text pasted",
  play: paste(ABOUT, false),
};

/** Over the limit: the count turns red and says what to do instead. */
export const TooLong: Story = {
  name: "Too long to paste",
  args: { limits: { ...LIMITS, paste_characters: 120 } },
  play: paste(ABOUT, false),
};

/** While the server stores the text. */
export const Adding: Story = {
  name: "Adding the text",
  beforeEach: () => {
    server.use(
      http.post("*/api/ingestion/documents/text", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
  },
  play: paste(ABOUT, true),
};

/** Added: the box empties and says so. */
export const Added: Story = {
  name: "Text added",
  play: paste(ABOUT, true),
};

/** The same text again: it is not added twice. */
export const AlreadyThere: Story = {
  name: "Text already in the documents",
  play: async (context) => {
    await paste(ABOUT, true)(context);
    await within(context.canvasElement).findByText("Text added.");
    await paste(ABOUT, true)(context);
  },
};

/** A text that looks like it holds a key: added, with the word to check it in the list. */
export const AddedWithAPossibleSecret: Story = {
  name: "Added, with a possible secret to check",
  play: paste(
    "Notes from the migration. Old access key AKIAEXAMPLEKEY0000000 was rotated in March.",
    true,
  ),
};

/** The server refuses: its sentence shows and the text stays in the box. */
export const Refused: Story = {
  name: "The server refuses the text",
  beforeEach: () => {
    server.use(
      http.post("*/api/ingestion/documents/text", () =>
        HttpResponse.json(
          {
            detail:
              "Your documents use 100 MB, the most this app keeps for one person. Delete a document first.",
          },
          { status: 413 },
        ),
      ),
    );
  },
  play: paste(ABOUT, true),
};

/** On a phone the count goes above the button. */
export const OnAPhone: Story = {
  name: "On a phone",
  play: paste(ABOUT, false),
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
