import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, HttpResponse } from "msw";
import { useState } from "react";
import { fn, userEvent, within } from "storybook/test";

import { BulkApplyDialog } from "@/components/shared/apply/bulk-apply";
import { Button } from "@/components/ui/button";
import { POSTINGS } from "@/design-system/fixtures/postings";
import {
  bulkPreview,
  bulkStarted,
  fakeBulkApply,
  LEFT_OUT,
} from "@/design-system/fixtures/shared-apply";
import { withApp } from "@/design-system/with-app";

const IDS = POSTINGS.slice(0, 7).map((job) => job.id);

const meta = {
  title: "Shared/Apply/Bulk apply",
  component: BulkApplyDialog,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    at: "/jobs",
    docs: {
      // Each story in its own frame on the docs page, so an open dialog covers only its own.
      story: { inline: false, height: "760px" },
      description: {
        component:
          "Apply to the postings selected on the Jobs list or map: one confirmation names every posting the click applies to, with its documents ready or written first, what the review box makes it do, and apart each posting it leaves out with its reason. Apply to N is the approval of exactly those.",
      },
    },
  },
  args: { request: { ids: IDS, key: 1 }, onClose: fn(), onApplied: fn() },
  beforeEach: () => {
    fakeBulkApply();
  },
} satisfies Meta<typeof BulkApplyDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

const applyToAll = async () => {
  const dialog = within(document.body);
  await userEvent.click(
    await dialog.findByRole("button", { name: /^Apply to \d+$/ }),
  );
};

/** Four postings: two ready, two written first with their estimate, one by e-mail; the assistant fills and sends each. */
export const ApplyToTheSelection: Story = {
  name: "Apply to the selected postings",
};

/** With the review box ticked: the assistant fills each form, then the person checks and sends it. */
export const ReviewEachForm: Story = {
  name: "Review each form before it is sent",
  beforeEach: () => {
    fakeBulkApply({
      preview: () =>
        HttpResponse.json(bulkPreview({ review_before_send: true })),
    });
  },
};

/** Some selected postings are left out, each with its reason. */
export const SomeLeftOut: Story = {
  name: "Some postings are left out",
  beforeEach: () => {
    fakeBulkApply({
      preview: () => HttpResponse.json(bulkPreview({ left_out: LEFT_OUT })),
    });
  },
};

/** More selected than one click takes: the first ones go, the others are selected after. */
export const PastTheLimit: Story = {
  name: "More than one click takes",
  beforeEach: () => {
    const preview = bulkPreview();
    fakeBulkApply({
      preview: () =>
        HttpResponse.json({
          ...preview,
          limit: 4,
          beyond: POSTINGS.slice(4, 7).map((job) => ({
            posting_id: job.id,
            company: job.company,
            title: job.title,
          })),
        }),
    });
  },
};

/** Every posting is left out: there is nothing to apply to, only Cancel. */
export const NoneCanBeApplied: Story = {
  name: "None can be applied to",
  beforeEach: () => {
    fakeBulkApply({
      preview: () =>
        HttpResponse.json(
          bulkPreview({ items: [], left_out: LEFT_OUT, estimate: null }),
        ),
    });
  },
};

/** Reading the selection: the list comes once, and never moves under the person's eyes. */
export const Reading: Story = {
  name: "Reading the selection",
  beforeEach: () => {
    fakeBulkApply({
      preview: async () => {
        await delay("infinite");
        return HttpResponse.json({});
      },
    });
  },
};

/** The selection could not be read: Try again. */
export const ReadFails: Story = {
  name: "The selection could not be read",
  beforeEach: () => {
    fakeBulkApply({
      preview: () =>
        HttpResponse.json(
          { detail: "The server did not answer in time." },
          { status: 504 },
        ),
    });
  },
};

/** After the click, the buttons wait while each posting starts. */
export const Applying: Story = {
  name: "Applying",
  beforeEach: () => {
    fakeBulkApply({
      start: async () => {
        await delay("infinite");
        return HttpResponse.json({});
      },
    });
  },
  play: applyToAll,
};

/** Every posting started: follow each one in Applications. */
export const AllStarted: Story = {
  name: "Every posting started",
  play: applyToAll,
};

/** One was left out at the click, with the api's reason. */
export const SomeLeftOutAtTheClick: Story = {
  name: "Some left out at the click",
  beforeEach: () => {
    fakeBulkApply({
      start: () => HttpResponse.json(bulkStarted(bulkPreview(), 1)),
    });
  },
  play: applyToAll,
};

/** Nothing started at the click. */
export const NothingStarted: Story = {
  name: "Nothing was started",
  beforeEach: () => {
    const preview = bulkPreview();
    fakeBulkApply({
      start: () =>
        HttpResponse.json(bulkStarted(preview, preview.items.length)),
    });
  },
  play: applyToAll,
};

/** The settings changed since the list was read: the server says so and the confirmation reads again. */
export const SettingsChanged: Story = {
  name: "The settings changed before the click",
  beforeEach: () => {
    fakeBulkApply({
      start: () =>
        HttpResponse.json(
          {
            detail:
              "Your review box changed since this list was read. Check what the click does now, then apply again.",
            code: "settings_changed",
          },
          { status: 409 },
        ),
    });
  },
  play: applyToAll,
};

/** The answer to the click was lost: some may have started, Applications shows which. */
export const ClickNotConfirmed: Story = {
  name: "The click could not be confirmed",
  beforeEach: () => {
    fakeBulkApply({
      start: () =>
        HttpResponse.json(
          { detail: "The server did not answer in time." },
          { status: 504 },
        ),
    });
  },
  play: applyToAll,
};

/** Opened from the bulk bar's Apply, as on the Jobs list. */
export const OpenFromTheBulkBar: Story = {
  name: "Open it from the bulk bar",
  args: { request: null },
  render: function Render(args) {
    const [request, setRequest] = useState(args.request);
    return (
      <>
        <Button
          size="sm"
          onClick={() => {
            setRequest({ ids: IDS, key: Date.now() });
          }}
        >
          Apply to 7 selected
        </Button>
        <BulkApplyDialog
          {...args}
          request={request}
          onClose={() => {
            setRequest(null);
          }}
        />
      </>
    );
  },
};
