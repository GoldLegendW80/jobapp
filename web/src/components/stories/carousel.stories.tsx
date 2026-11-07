import type { Meta, StoryObj } from "@storybook/react-vite";
import { CheckIcon } from "lucide-react";

import { AspectRatio } from "@/components/ui/aspect-ratio";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import { cn } from "@/lib/utils";

const TEMPLATES = [
  "Classic",
  "Sidebar",
  "Compact",
  "Modern",
  "Timeline",
  "Two columns",
  "Minimal",
];

function Template({ name, chosen }: { name: string; chosen: boolean }) {
  return (
    <Button
      variant="outline"
      aria-pressed={chosen}
      className={cn(
        "h-auto w-full flex-col items-stretch gap-1 rounded-lg p-1 text-left font-normal whitespace-normal",
        chosen && "border-primary ring-2 ring-primary",
      )}
    >
      <AspectRatio
        ratio={210 / 297}
        className="rounded-sm border bg-muted"
        aria-hidden="true"
      />
      <span className="flex items-center justify-between gap-1 px-1 pb-1">
        <span className="truncate font-medium">{name}</span>
        {chosen ? (
          <Badge variant="secondary">
            <CheckIcon aria-hidden="true" />
            In use
          </Badge>
        ) : null}
      </span>
    </Button>
  );
}

const meta = {
  title: "Components/Data display/Carousel",
  component: Carousel,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A row of items wider than the screen, moved by swipe, by the arrow keys, or by Previous and Next: the template strip of a document. Each item stays a real control; the carousel only moves them.",
      },
    },
  },
} satisfies Meta<typeof Carousel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The template strip: three at a time, the one in use marked. */
export const Typical: Story = {
  render: () => (
    <div className="max-w-xl px-12">
      <Carousel opts={{ align: "start" }} aria-label="Templates">
        <CarouselContent className="py-1">
          {TEMPLATES.map((name) => (
            <CarouselItem key={name} className="basis-1/3">
              <Template name={name} chosen={name === "Sidebar"} />
            </CarouselItem>
          ))}
        </CarouselContent>
        <CarouselPrevious />
        <CarouselNext />
      </Carousel>
    </div>
  ),
};

/** On a phone: one and a half in view says there is more, and a swipe moves them. */
export const OnAPhone: Story = {
  render: () => (
    <div className="max-w-sm px-12">
      <Carousel opts={{ align: "start" }} aria-label="Templates">
        <CarouselContent className="py-1">
          {TEMPLATES.map((name) => (
            <CarouselItem key={name} className="basis-2/3">
              <Template name={name} chosen={name === "Classic"} />
            </CarouselItem>
          ))}
        </CarouselContent>
        <CarouselPrevious />
        <CarouselNext />
      </Carousel>
    </div>
  ),
};

/** Few items: they all fit, so Previous and Next are disabled. */
export const AllInView: Story = {
  render: () => (
    <div className="max-w-xl px-12">
      <Carousel opts={{ align: "start" }} aria-label="Templates">
        <CarouselContent className="py-1">
          {TEMPLATES.slice(0, 3).map((name) => (
            <CarouselItem key={name} className="basis-1/3">
              <Template name={name} chosen={name === "Classic"} />
            </CarouselItem>
          ))}
        </CarouselContent>
        <CarouselPrevious />
        <CarouselNext />
      </Carousel>
    </div>
  ),
};
