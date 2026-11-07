import type { Meta, StoryObj } from "@storybook/react-vite";

import { StepDocuments } from "@/components/shared/ingestion/step-documents";
import { Card, CardContent } from "@/components/ui/card";
import { fakeCamilleDocuments } from "@/design-system/fixtures/camille-ingestion";
import {
  everyDocumentState,
  failDocuments,
} from "@/design-system/fixtures/shared-ingestion";
import { withApp } from "@/design-system/with-app";
import { fakeDocuments } from "@/test/ingestion";

const meta = {
  title: "Shared/Ingestion/Step documents",
  component: StepDocuments,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The Documents step: add files or paste a text, then the documents the app keeps, each with its preview and deletion. In Fill my profile beside the list, and stacked in the welcome wizard.",
      },
    },
  },
  beforeEach: () => {
    fakeCamilleDocuments();
  },
} satisfies Meta<typeof StepDocuments>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The ways to add on the left, Camille's CV and notes in the side rail. */
export const YourDocuments: Story = { name: "Your documents" };

/** A file being read, one to check, one not read, one waiting: the list follows them. */
export const EveryState: Story = {
  name: "Documents in every state",
  beforeEach: () => {
    fakeDocuments(everyDocumentState());
  },
};

/** Nothing added yet. */
export const NoDocumentYet: Story = {
  name: "No document yet",
  beforeEach: () => {
    fakeDocuments([]);
  },
};

/** While the list loads, its place keeps the layout. */
export const Loading: Story = {
  name: "Loading the documents",
  beforeEach: () => {
    failDocuments("loading");
  },
};

/** The list could not be loaded: Retry, and the ways to add still work. */
export const CouldNotLoad: Story = {
  name: "The documents could not be loaded",
  beforeEach: () => {
    failDocuments("error");
  },
};

/** Inside a card already (the welcome wizard): everything stacked, no surface of its own. */
export const InsideACard: Story = {
  name: "Inside a card",
  args: { flat: true },
  decorators: [
    (Story) => (
      <Card>
        <CardContent>
          <Story />
        </CardContent>
      </Card>
    ),
  ],
};

/** On a phone the list comes under the ways to add. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
