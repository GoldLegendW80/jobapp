import type { Meta, StoryObj } from "@storybook/react-vite";
import { CheckIcon, XIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { FitBadge } from "@/components/shared/jobs/badges";
import { cn } from "@/lib/utils";

const meta = {
  title: "Patterns/Do and don't",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The rules of the system that are easiest to break, each shown right and wrong. In copy, the repository's own rule also holds: no dash as a separator in any text a person reads; use a comma, a colon or a new sentence.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

function Pair({
  rule,
  good,
  bad,
}: {
  rule: string;
  good: ReactNode;
  bad: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">{rule}</h3>
      <div className="grid gap-4 md:grid-cols-2">
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
            <div className="flex min-h-16 flex-wrap items-center gap-3">
              {example}
            </div>
          </figure>
        ))}
      </div>
    </section>
  );
}

/** The black pill is spent once per group, on the thing the person came to do. */
export const OnePrimaryAction: Story = {
  render: () => (
    <Pair
      rule="One primary action per group"
      good={
        <>
          <Button>Apply</Button>
          <Button variant="outline">Pass</Button>
        </>
      }
      bad={
        <>
          <Button>Apply</Button>
          <Button>Pass</Button>
          <Button>Write a CV</Button>
        </>
      }
    />
  ),
};

/** A tone supports a word; it never replaces one. */
export const WordsNotColorAlone: Story = {
  render: () => (
    <Pair
      rule="Say it in words, not with color alone"
      good={<FitBadge fit={76} />}
      bad={
        <span aria-hidden="true" className="size-3 rounded-full bg-success" />
      }
    />
  ),
};

/** Orange means "this matters". Spent on every button, it stops meaning anything. */
export const OrangeIsRare: Story = {
  render: () => (
    <Pair
      rule="Orange marks what matters, never an ordinary action"
      good={
        <>
          <Button>Apply</Button>
          <Button variant="secondary">Pass</Button>
          <Badge variant="brand">3 wait for you</Badge>
        </>
      }
      bad={
        <>
          <Button className="bg-brand text-brand-foreground hover:bg-brand/90">
            Apply
          </Button>
          <Button className="bg-brand text-brand-foreground hover:bg-brand/90">
            Pass
          </Button>
        </>
      }
    />
  ),
};

/** White on grey already lifts a card. A shadow says it floats, and a card does not. */
export const FlatCards: Story = {
  render: () => (
    <Pair
      rule="Cards are white on the canvas, without a shadow"
      good={
        <Card className="w-full">
          <CardHeader>
            <CardTitle>Product analyst</CardTitle>
            <CardDescription>Atelier Brume, Paris</CardDescription>
          </CardHeader>
        </Card>
      }
      bad={
        <Card className="w-full shadow-lg">
          <CardHeader>
            <CardTitle>Product analyst</CardTitle>
            <CardDescription>Atelier Brume, Paris</CardDescription>
          </CardHeader>
        </Card>
      }
    />
  ),
};
