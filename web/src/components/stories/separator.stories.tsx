import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

const meta = {
  title: "Components/Data display/Separator",
  component: Separator,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A hairline between groups inside one surface: the sections of a posting, the links of a toolbar. Space (gap) already parts unrelated blocks; add a line only where two groups would run together.",
      },
    },
  },
} satisfies Meta<typeof Separator>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Between the summary of a posting and its details. */
export const Typical: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4 text-sm">
      <div className="flex flex-col gap-1">
        <span className="font-medium">Senior data engineer</span>
        <span className="text-muted-foreground">
          Lumen Freight, Lyon, Hybrid
        </span>
      </div>
      <Separator />
      <p>
        55,000 to 65,000 EUR a year, permanent. The team moves freight data from
        40 depots into one warehouse and wants someone to own its pipelines.
      </p>
    </div>
  ),
};

/** Vertical between short actions on one line. Give the line a height, it has none of its own. */
export const Vertical: Story = {
  render: () => (
    <div className="flex h-5 items-center gap-3 text-sm">
      <Button variant="link" className="h-auto p-0">
        Posting page
      </Button>
      <Separator orientation="vertical" />
      <Button variant="link" className="h-auto p-0">
        Company site
      </Button>
      <Separator orientation="vertical" />
      <Button variant="link" className="h-auto p-0">
        Report a problem
      </Button>
    </div>
  ),
};

/** Semantic when it parts sections a screen reader should hear as separate: decorative false. */
export const Semantic: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4 text-sm">
      <p>Your profile: 23 skills, 8 roles, 2 languages.</p>
      <Separator decorative={false} />
      <p>Your search: data engineering roles in Lyon, Paris or remote.</p>
    </div>
  ),
};

/** Many groups in a row: one line between each, never two lines in a row. */
export const ManySections: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-3 text-sm">
      {["Pay", "Work mode", "Contract", "Skills you match"].map(
        (label, index) => (
          <div key={label} className="flex flex-col gap-3">
            {index > 0 ? <Separator /> : null}
            <div className="flex justify-between gap-4">
              <span className="text-muted-foreground">{label}</span>
              <span>
                {
                  [
                    "55,000 to 65,000 EUR a year",
                    "Hybrid, Lyon",
                    "Permanent",
                    "7 of 9",
                  ][index]
                }
              </span>
            </div>
          </div>
        ),
      )}
    </div>
  ),
};
