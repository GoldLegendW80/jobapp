import type { Meta, StoryObj } from "@storybook/react-vite";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const meta = {
  title: "Foundations/Spacing",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A 4 px grid, Tailwind's own scale. Spacing groups before borders do: things that belong together sit close, groups are separated by clearly more space, and a heading sits closer to what it introduces than to what came before. Layout uses gap, never space-x or space-y.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const SCALE = [1, 1.5, 2, 3, 4, 6, 8, 10, 16] as const;

/** The steps in use. Anything else is a smell. */
export const Scale: Story = {
  render: () => (
    <div className="flex flex-col gap-2">
      {SCALE.map((step) => (
        <div key={step} className="flex items-center gap-4">
          <code className="w-16 text-xs text-muted-foreground tabular-nums">
            {step * 4}px
          </code>
          <div
            className="h-3 rounded-sm bg-primary"
            style={{ width: `${step * 4}px` }}
          />
          <code className="text-xs">gap-{step}</code>
        </div>
      ))}
    </div>
  ),
};

const LADDER = [
  [
    "Inside a control",
    "gap-1.5 to gap-2, 6 to 8 px",
    "Icon to label in a button",
  ],
  [
    "Between related items",
    "gap-2, 8 px",
    "Badges of a posting, buttons of a group",
  ],
  [
    "Between fields",
    "gap-4 to gap-6, 16 to 24 px",
    "The fields of a form, rows of a card",
  ],
  [
    "Between groups",
    "gap-6 to gap-8, 24 to 32 px",
    "Card sections, form sections",
  ],
  [
    "Between page sections",
    "gap-10, 40 px and more",
    "Sections of a page; the page gutter is 16 px on a phone",
  ],
] as const;

/** Which step to use, by relationship rather than by number. */
export const Ladder: Story = {
  render: () => (
    <div className="flex flex-col gap-6">
      <dl className="grid max-w-3xl gap-x-6 gap-y-3 text-sm sm:grid-cols-[12rem_14rem_1fr]">
        {LADDER.map(([what, step, example]) => (
          <div key={what} className="contents">
            <dt className="font-medium">{what}</dt>
            <dd>
              <code className="text-xs">{step}</code>
            </dd>
            <dd className="text-muted-foreground">{example}</dd>
          </div>
        ))}
      </dl>
      <div className="flex max-w-md flex-col gap-4 rounded-lg border bg-card p-6">
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-semibold">Product analyst</h3>
          <p className="text-sm text-muted-foreground">
            Atelier Brume, Paris, remote friendly
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">Permanent</Badge>
          <Badge variant="secondary">SQL</Badge>
          <Badge variant="secondary">Looker</Badge>
        </div>
        <div className="flex gap-2">
          <Button size="sm">Apply</Button>
          <Button size="sm" variant="outline">
            Pass
          </Button>
        </div>
      </div>
    </div>
  ),
};
