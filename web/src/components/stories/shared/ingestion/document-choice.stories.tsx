import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { DocumentChoice } from "@/components/shared/ingestion/run-start/document-choice";
import { camilleDocuments } from "@/design-system/fixtures/camille-ingestion";
import {
  everyDocumentState,
  LONG_NAME_DOCUMENT,
} from "@/design-system/fixtures/shared-ingestion";
import type { IngestionDocument } from "@/features/ingestion/api/documents-api";
import { canBeChosen } from "@/features/ingestion/helpers/run-selection";

const meta = {
  title: "Shared/Ingestion/Document choice",
  component: DocumentChoice,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The documents a run may read, a box each, Select all and how many are selected: in Run start, before the start button.",
      },
    },
  },
  args: {
    items: camilleDocuments(),
    selected: [],
    onToggle: () => undefined,
    onChooseAll: () => undefined,
  },
} satisfies Meta<typeof DocumentChoice>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing ticked yet. */
export const NoneChosen: Story = { name: "None chosen" };

/** One of two chosen. */
export const OneChosen: Story = {
  name: "One chosen",
  args: { selected: ["doc-notes"] },
};

/** All chosen: the button clears the selection. */
export const AllChosen: Story = {
  name: "All chosen",
  args: { selected: ["doc-cv", "doc-notes"] },
};

/** A document being read, one not read, one waiting for a reader: their boxes are off, with why. */
export const SomeCannotBeChosen: Story = {
  name: "Some cannot be chosen yet",
  args: { items: everyDocumentState() },
};

/** A single document: no Select all. */
export const OneDocument: Story = {
  name: "A single document",
  args: { items: camilleDocuments().slice(0, 1) },
};

function ChoiceInOrder({ items }: { items: IngestionDocument[] }) {
  const [selected, setSelected] = useState<readonly string[]>([]);
  return (
    <DocumentChoice
      items={items}
      selected={selected}
      onToggle={(id, on) => {
        setSelected((old) =>
          on
            ? [...old.filter((one) => one !== id), id]
            : old.filter((one) => one !== id),
        );
      }}
      onChooseAll={setSelected}
    />
  );
}

/** Tick the documents in the order the run reads them, or all at once. */
export const ChooseInOrder: Story = {
  name: "Choose in order",
  render: () => (
    <ChoiceInOrder
      items={[LONG_NAME_DOCUMENT, ...everyDocumentState()].filter(
        (one) => canBeChosen(one) || one.status === "failed",
      )}
    />
  ),
};

/** On a phone long names wrap under their box. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: { items: [LONG_NAME_DOCUMENT, ...everyDocumentState()] },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
