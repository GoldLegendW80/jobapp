import type { Meta, StoryObj } from "@storybook/react-vite";
import { ArrowLeftIcon, ArrowRightIcon, Undo2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const meta = {
  title: "Components/Actions/Kbd",
  component: Kbd,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A key on the keyboard, named inside a sentence that says what it does. Shown only where a keyboard is likely (a fine pointer), never as the only way to act.",
      },
    },
  },
  args: { children: "U" },
} satisfies Meta<typeof Kbd>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The keys of the deck, under the card. */
export const Typical: Story = {
  render: () => (
    <p className="text-xs text-muted-foreground">
      Keys:{" "}
      <Kbd>
        <ArrowLeftIcon aria-hidden="true" />
        <span className="sr-only">left arrow</span>
      </Kbd>{" "}
      to pass,{" "}
      <Kbd>
        <ArrowRightIcon aria-hidden="true" />
        <span className="sr-only">right arrow</span>
      </Kbd>{" "}
      to like, <Kbd>U</Kbd> to undo the last one.
    </p>
  ),
};

/** Keys pressed together sit in a KbdGroup, joined by a plus. Name both platforms when they differ. */
export const Group: Story = {
  render: () => (
    <p className="text-sm text-muted-foreground">
      The link is selected: press{" "}
      <KbdGroup>
        <Kbd>Ctrl</Kbd>
        <span>+</span>
        <Kbd>C</Kbd>
      </KbdGroup>
      , or{" "}
      <KbdGroup>
        <Kbd>Cmd</Kbd>
        <span>+</span>
        <Kbd>C</Kbd>
      </KbdGroup>{" "}
      on a Mac.
    </p>
  ),
};

/** An arrow key is an icon with its name for screen readers beside it. */
export const WithIcon: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Kbd>
        <ArrowLeftIcon aria-hidden="true" />
        <span className="sr-only">left arrow</span>
      </Kbd>
      <Kbd>
        <ArrowRightIcon aria-hidden="true" />
        <span className="sr-only">right arrow</span>
      </Kbd>
      <Kbd>Enter</Kbd>
      <Kbd>Esc</Kbd>
    </div>
  ),
};

/** Inside a Tooltip the key takes the tooltip's colors by itself. */
export const InTooltip: Story = {
  render: () => (
    <div className="ps-8 pt-12">
      <Tooltip open>
        <TooltipTrigger asChild>
          <Button variant="outline" size="icon" aria-label="Undo last">
            <Undo2Icon />
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          Undo last <Kbd>U</Kbd>
        </TooltipContent>
      </Tooltip>
    </div>
  ),
};

/** In running text the key keeps the line height; a sentence of the chat names the key it waits for. */
export const InSentence: Story = {
  render: () => (
    <p className="max-w-sm text-sm text-muted-foreground">
      <Kbd>Enter</Kbd> sends your message once the answer is complete.{" "}
      <KbdGroup>
        <Kbd>Shift</Kbd>
        <span>+</span>
        <Kbd>Enter</Kbd>
      </KbdGroup>{" "}
      starts a new line.
    </p>
  ),
};
