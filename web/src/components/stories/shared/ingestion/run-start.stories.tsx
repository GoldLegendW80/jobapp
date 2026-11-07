import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { RunStart } from "@/components/shared/ingestion/run-start";
import { Card, CardContent } from "@/components/ui/card";
import {
  camilleDocuments,
  doneRun,
} from "@/design-system/fixtures/camille-ingestion";
import {
  CONSENT_ASKED,
  everyDocumentState,
  failRuns,
  fakeShownRuns,
} from "@/design-system/fixtures/shared-ingestion";
import { withApp } from "@/design-system/with-app";
import { fakeDocuments } from "@/test/ingestion";

const selectAll = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    await within(canvasElement).findByRole("button", { name: "Select all" }),
  );
};

const meta = {
  title: "Shared/Ingestion/Run start",
  component: RunStart,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Choose the documents the agent reads, in order, then start the run from a button that carries its token estimate: the Review step of Fill my profile and the welcome wizard. Nothing is sent before that click.",
      },
    },
  },
  beforeEach: () => {
    fakeShownRuns();
  },
} satisfies Meta<{ flat?: boolean }>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The first reading: the documents to tick, and what the button waits for. */
export const ChooseTheDocuments: Story = {
  name: "Choose the documents",
};

/** Both documents chosen: the button names the tokens, under it where the run pauses. */
export const DocumentsChosen: Story = {
  name: "Documents chosen, the estimate on the button",
  play: selectAll,
};

/** The first run ever: the consent box comes before the button, which waits for the tick. */
export const FirstRunAsksConsent: Story = {
  name: "First run, consent asked",
  beforeEach: () => {
    fakeShownRuns([], CONSENT_ASKED);
  },
  play: selectAll,
};

/** A run ended before: the heading offers another reading. */
export const AnotherReading: Story = {
  name: "Another reading after a run",
  beforeEach: () => {
    fakeShownRuns([doneRun()]);
  },
};

/** Documents being read, failed or waiting for a reader cannot be ticked, and say why. */
export const SomeCannotBeReadYet: Story = {
  name: "Some documents cannot be read yet",
  beforeEach: () => {
    fakeShownRuns();
    fakeDocuments(everyDocumentState());
  },
};

/** No document yet: where to add them first. */
export const NoDocumentYet: Story = {
  name: "No document yet",
  beforeEach: () => {
    fakeShownRuns();
    fakeDocuments([]);
  },
};

/** While the documents and the runs load. */
export const Loading: Story = {
  name: "Loading",
  beforeEach: () => {
    failRuns("loading");
  },
};

/** The runs could not be loaded: this step says so. */
export const CouldNotLoad: Story = {
  name: "This step could not be loaded",
  beforeEach: () => {
    failRuns("error");
  },
};

/** Inside a card already (the welcome wizard): no surface of its own. */
export const InsideACard: Story = {
  name: "Inside a card",
  args: { flat: true },
  play: selectAll,
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

/** On a phone the button takes the width and its estimate wraps. */
export const OnAPhone: Story = {
  name: "On a phone",
  beforeEach: () => {
    fakeShownRuns([], CONSENT_ASKED);
    fakeDocuments([...camilleDocuments(), ...everyDocumentState().slice(0, 1)]);
  },
  play: selectAll,
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
