import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { WhatYouWant } from "@/components/shared/jobs/search-setup/what-you-want";
import { WithSearchForm } from "@/design-system/fixtures/job-harnesses";
import { findJobsSearch } from "@/design-system/fixtures/jobs-actions";
import { withApp } from "@/design-system/with-app";
import { EMPTY, type Search } from "@/features/profile/api/profile-api";

/** The step over `search`, its More options open or folded, on the setup dialog's surface. */
function Step({
  search = findJobsSearch(),
  open = false,
}: {
  search?: Search;
  open?: boolean;
}) {
  const [more, setMore] = useState(open);
  const [instructions, setInstructions] = useState(
    search.interactive_instructions ?? "",
  );
  return (
    <div className="max-w-2xl rounded-xl bg-card px-6 pb-6">
      <WithSearchForm search={search}>
        {(form) => (
          <WhatYouWant
            form={form}
            more={more}
            onMoreChange={setMore}
            instructions={instructions}
            onInstructionsChange={setInstructions}
          />
        )}
      </WithSearchForm>
    </div>
  );
}

const meta = {
  title: "Shared/Jobs/What you want",
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Step 2 of Find jobs: the job titles and places wanted, and under More options the words not wanted, the criteria, what the person aims for and instructions for the assistant.",
      },
      story: { inline: false, height: "720px" },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Camille's titles and places. */
export const Typical: Story = {
  name: "Titles and places",
  render: () => <Step />,
};

/** More options open, with what it holds. */
export const MoreOptions: Story = {
  name: "More options open",
  render: () => <Step open />,
};

/** A first search: nothing typed yet. */
export const Empty: Story = {
  name: "Nothing asked for yet",
  render: () => <Step search={structuredClone(EMPTY.search)} />,
};

/** On a phone the step keeps one edge and the chips wrap. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: () => (
    <div className="max-w-sm">
      <Step />
    </div>
  ),
};
