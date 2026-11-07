import type { Meta, StoryObj } from "@storybook/react-vite";
import { Globe, Info } from "lucide-react";

import {
  BLOCK,
  BLOCKS,
  BOX,
  COLUMN,
  Glyph,
  HANG,
  StepNumber,
} from "@/components/shared/jobs/setup-grid";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

const meta = {
  title: "Shared/Jobs/Setup grid",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The grid of Find jobs: one left edge for every label, help, field and list, with step numbers, icons and radio buttons hanging in a gutter before it. Its parts (COLUMN, HANG, BOX, Glyph, StepNumber) build the setup's two steps.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** A step: its number in the gutter, every line after it on the same edge. */
export const OneEdge: Story = {
  name: "Lines on one edge",
  render: () => (
    <section className={cn(COLUMN, BLOCKS, "max-w-xl")}>
      <h3
        className={cn(
          HANG,
          "items-center text-base font-semibold tracking-tight",
        )}
      >
        <StepNumber step={1} />
        Where to search
      </h3>
      <p className={cn(HANG, "text-sm")}>
        <Glyph line="h-5">
          <Globe className="size-4 text-muted-foreground" />
        </Glyph>
        A job website, like LinkedIn or Indeed
      </p>
      <div className={BLOCK}>
        <Label htmlFor="grid-address">Website address</Label>
        <div className="flex gap-2">
          <Input id="grid-address" placeholder="https://" />
          <Button variant="outline">Add</Button>
        </div>
      </div>
    </section>
  ),
};

/** A box with a border reaches past the gutter, its glyph on the same axis as the others. */
export const ABox: Story = {
  name: "A box in the column",
  render: () => (
    <section className={cn(COLUMN, BLOCKS, "max-w-xl")}>
      <h3
        className={cn(
          HANG,
          "items-center text-base font-semibold tracking-tight",
        )}
      >
        <StepNumber step={2} />
        What you want
      </h3>
      <p className={cn(BOX, "py-2 text-sm")}>
        <Glyph line="h-5">
          <Info className="size-4 text-muted-foreground" />
        </Glyph>
        The app keeps the postings whose title matches one you want.
      </p>
    </section>
  ),
};
