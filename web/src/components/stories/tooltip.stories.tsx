import type { Meta, StoryObj } from "@storybook/react-vite";
import { ArrowRightIcon, HeartIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// Shown in its own iframe on the docs page, so the open tooltip does not cover the page.
const OPEN_FOR_REVIEW = {
  layout: "centered",
  docs: { story: { inline: false, height: "420px" } },
} as const;

function LikeButton({ defaultOpen }: { defaultOpen?: boolean }) {
  return (
    <Tooltip defaultOpen={defaultOpen}>
      <TooltipTrigger asChild>
        <Button variant="outline" size="icon" aria-label="Like">
          <HeartIcon />
        </Button>
      </TooltipTrigger>
      <TooltipContent>Like</TooltipContent>
    </Tooltip>
  );
}

const meta = {
  title: "Components/Overlays/Tooltip",
  component: Tooltip,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Names an icon-only control, or gives the full text of a truncated one, on hover and keyboard focus. One short phrase. Touch screens never show it, so it is never the only place for something the person needs.",
      },
    },
  },
} satisfies Meta<typeof Tooltip>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The name of an icon button, the same words as its aria-label. Hover or focus it. */
export const Typical: Story = {
  render: () => <LikeButton />,
};

/** Open, for review. */
export const Open: Story = {
  render: () => <LikeButton defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** With the key that does the same, on the deck where the arrows like and pass. */
export const WithShortcut: Story = {
  render: () => (
    <Tooltip defaultOpen>
      <TooltipTrigger asChild>
        <Button variant="outline" size="icon" aria-label="Like">
          <HeartIcon />
        </Button>
      </TooltipTrigger>
      <TooltipContent className="flex items-center gap-2">
        Like
        <Kbd>
          <ArrowRightIcon aria-hidden="true" />
          <span className="sr-only">right arrow</span>
        </Kbd>
      </TooltipContent>
    </Tooltip>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** Top by default; another side only when top would cover what the person is reading. */
export const Sides: Story = {
  render: () => (
    <div className="grid grid-cols-2 gap-x-28 gap-y-16">
      {(["top", "right", "bottom", "left"] as const).map((side) => (
        <Tooltip key={side} open>
          <TooltipTrigger asChild>
            <Button variant="outline" size="icon" aria-label="Pass">
              <XIcon />
            </Button>
          </TooltipTrigger>
          <TooltipContent side={side}>Pass</TooltipContent>
        </Tooltip>
      ))}
    </div>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** A disabled button takes no hover: wrap it in a focusable span so the tooltip can say why. */
export const OnDisabled: Story = {
  render: () => (
    <Tooltip defaultOpen>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="inline-flex rounded-full">
          <Button disabled>Apply</Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        Apply opens once the CV for this posting is ready.
      </TooltipContent>
    </Tooltip>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** The full text of a truncated title. A long tooltip wraps at a readable width. */
export const LongText: Story = {
  render: () => (
    <Tooltip defaultOpen>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className="block max-w-56 truncate text-sm font-medium"
        >
          Senior staff machine learning platform engineer, recommendations and
          search infrastructure
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        Senior staff machine learning platform engineer, recommendations and
        search infrastructure
      </TooltipContent>
    </Tooltip>
  ),
  parameters: OPEN_FOR_REVIEW,
};
