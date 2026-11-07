import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  CheckIcon,
  ChevronDownIcon,
  MinusIcon,
  PlusIcon,
  RotateCcwIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  ButtonGroup,
  ButtonGroupSeparator,
  ButtonGroupText,
} from "@/components/ui/button-group";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

function MapZoom({ disabled = false }: { disabled?: boolean }) {
  return (
    <ButtonGroup aria-label="Zoom of the map">
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="Zoom in"
        disabled={disabled}
      >
        <PlusIcon />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="Zoom out"
        disabled={disabled}
      >
        <MinusIcon />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="Show the whole world"
        disabled={disabled}
      >
        <RotateCcwIcon />
      </Button>
    </ButtonGroup>
  );
}

const meta = {
  title: "Components/Actions/Button group",
  component: ButtonGroup,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Buttons that act on the same thing, joined into one pill: the zoom of the map, an action and the menu of its variants. Unrelated actions stay apart, with space between them. A field with a button inside it is an InputGroup, not a button group.",
      },
    },
  },
} satisfies Meta<typeof ButtonGroup>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The map's zoom: in, out, the whole world. */
export const Typical: Story = {
  render: () => <MapZoom />,
};

/** An action and its variants: Apply, and a menu for the other ways to apply. */
export const SplitButton: Story = {
  render: () => (
    <ButtonGroup aria-label="Apply">
      <Button>Apply</Button>
      <ButtonGroupSeparator className="bg-primary-foreground/30" />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon" aria-label="Other ways to apply">
            <ChevronDownIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuGroup>
            <DropdownMenuItem>Review the form first</DropdownMenuItem>
            <DropdownMenuItem>Apply on the site myself</DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </ButtonGroup>
  ),
};

/** A word that says what the buttons act on, joined to them. */
export const WithText: Story = {
  render: () => (
    <ButtonGroup aria-label="Zoom">
      <ButtonGroupText>Zoom</ButtonGroupText>
      <Button variant="outline" size="icon" aria-label="Zoom in">
        <PlusIcon />
      </Button>
      <Button variant="outline" size="icon" aria-label="Zoom out">
        <MinusIcon />
      </Button>
    </ButtonGroup>
  ),
};

/** sm beside a map or a table, default in a form. */
export const Sizes: Story = {
  render: () => (
    <div className="flex flex-col items-start gap-3">
      <MapZoom />
      <ButtonGroup aria-label="Zoom of the map">
        <Button variant="outline" size="icon" aria-label="Zoom in">
          <PlusIcon />
        </Button>
        <Button variant="outline" size="icon" aria-label="Zoom out">
          <MinusIcon />
        </Button>
      </ButtonGroup>
    </div>
  ),
};

/** Stacked, at the corner of the map on a phone. */
export const Vertical: Story = {
  render: () => (
    <ButtonGroup orientation="vertical" aria-label="Zoom of the map">
      <Button variant="outline" size="icon-sm" aria-label="Zoom in">
        <PlusIcon />
      </Button>
      <Button variant="outline" size="icon-sm" aria-label="Zoom out">
        <MinusIcon />
      </Button>
    </ButtonGroup>
  ),
};

/** Disabled together while the map loads. */
export const Disabled: Story = {
  render: () => <MapZoom disabled />,
};

function Pair({ good, bad }: { good: ReactNode; bad: ReactNode }) {
  return (
    <div className="grid max-w-3xl gap-4 md:grid-cols-2">
      {(
        [
          ["Do", good, CheckIcon, "text-success"],
          ["Don't", bad, XIcon, "text-destructive"],
        ] as const
      ).map(([label, example, Icon, tone]) => (
        <figure
          key={label}
          className="flex flex-col gap-3 rounded-lg border bg-card p-4"
        >
          <figcaption
            className={cn(
              "flex items-center gap-1.5 text-sm font-medium",
              tone,
            )}
          >
            <Icon aria-hidden="true" className="size-4" />
            {label}
          </figcaption>
          {example}
        </figure>
      ))}
    </div>
  );
}

/** Join actions on one thing; keep a primary action and a destructive one apart. */
export const DoAndDont: Story = {
  name: "Do and don't",
  render: () => (
    <Pair
      good={<MapZoom />}
      bad={
        <ButtonGroup aria-label="Actions on the posting">
          <Button>Apply</Button>
          <Button variant="destructive">
            <Trash2Icon />
            Delete
          </Button>
        </ButtonGroup>
      }
    />
  ),
};
