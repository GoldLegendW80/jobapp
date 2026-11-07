import type { Meta, StoryObj } from "@storybook/react-vite";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { ColorPair, Swatch } from "../color-pair";

const meta = {
  title: "Foundations/Color",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Neutral greys carry the app: a grey canvas, white cards, near-black for the action. One orange is spent on what matters: the featured figure, a chart, progress, a count that waits. Focus is a neutral ring, so it never reads as an error. Meaning colors say how a posting or an application is doing, always with words beside them. Switch the theme in the toolbar: every ratio is measured live.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const ROLES = [
  ["Canvas", "The grey every screen sits on", "background", "foreground"],
  ["Card", "Postings, panels, forms", "card", "card-foreground"],
  ["Popover", "Menus, tooltips, dialogs", "popover", "popover-foreground"],
  ["Primary", "The one action of a group", "primary", "primary-foreground"],
  [
    "Secondary",
    "Actions beside the primary",
    "secondary",
    "secondary-foreground",
  ],
  ["Muted", "Quiet surfaces, secondary text", "muted", "muted-foreground"],
  ["Accent", "Hovered rows and menu items", "accent", "accent-foreground"],
  [
    "Brand",
    "The featured figure, charts, progress",
    "brand",
    "brand-foreground",
  ],
  [
    "Mark",
    "Matched skills, cited quotes, selection",
    "highlight",
    "highlight-foreground",
  ],
  ["Success", "Solid: interview, top fit", "success", "success-foreground"],
  ["Warning", "Solid: waits for you", "warning", "warning-foreground"],
  ["Info", "Solid: information", "info", "info-foreground"],
  [
    "Destructive",
    "Solid: a loss to confirm",
    "destructive",
    "destructive-foreground",
  ],
] as const;

/** Every surface with the text that sits on it. Body text needs 4.5:1; a pair under it is shown in bold. */
export const Roles: Story = {
  render: () => (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {ROLES.map(([name, use, background, foreground]) => (
        <ColorPair
          key={name}
          name={name}
          use={use}
          background={background}
          foreground={foreground}
        />
      ))}
    </div>
  ),
};

const TONES = [
  {
    token: "success",
    text: "text-success",
    soft: "border-success/30 bg-success/10 text-success",
    solid: "bg-success text-success-foreground",
    words: ["Applied", "Interview", "Fit 74"],
    use: "Progress: applied, an interview, a good fit.",
  },
  {
    token: "warning",
    text: "text-warning",
    soft: "border-warning/30 bg-warning/10 text-warning",
    solid: "bg-warning text-warning-foreground",
    words: ["Waiting for you", "Staged", "Fit 52"],
    use: "Something waits for the person, or a fair fit.",
  },
  {
    token: "info",
    text: "text-info",
    soft: "border-info/30 bg-info/10 text-info",
    solid: "bg-info text-info-foreground",
    words: ["Shortlisted", "New", "3 sites"],
    use: "Neutral information that deserves a look.",
  },
  {
    token: "destructive",
    text: "text-destructive",
    soft: "border-destructive/30 bg-destructive/10 text-destructive",
    solid: "bg-destructive text-destructive-foreground",
    words: ["Rejected", "Failed", "Fit 18"],
    use: "A no, a failure, a low fit, an error.",
  },
] as const;

/** Each meaning in three strengths: text on the page, a soft chip for lists, a solid chip for the one state that matters most. Color is never the only clue: the word is always there. */
export const Meaning: Story = {
  render: () => (
    <div className="flex flex-col gap-6">
      {TONES.map((tone) => (
        <div
          key={tone.token}
          className="grid items-center gap-3 sm:grid-cols-[10rem_1fr]"
        >
          <div className="flex flex-col gap-0.5">
            <code className="text-sm">{tone.token}</code>
            <span className="text-xs text-muted-foreground">{tone.use}</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span className={cn("text-sm font-medium", tone.text)}>
              {tone.words[0]}
            </span>
            {tone.words.map((word) => (
              <Badge key={word} variant="outline" className={tone.soft}>
                {word}
              </Badge>
            ))}
            <Badge className={tone.solid}>{tone.words[1]}</Badge>
          </div>
        </div>
      ))}
    </div>
  ),
};

/** The soft orange mark: a skill that matches the profile, the sentence a proposal quotes, a CV that is ready, selected text. Never for decoration or a button. */
export const Mark: Story = {
  render: () => (
    <div className="flex max-w-prose flex-col gap-4">
      <p className="text-base leading-relaxed">
        We are looking for a data engineer to run our freight pricing pipelines.
        You know <mark>Python</mark> and <mark>dbt</mark>, you have shipped
        streaming jobs on <mark>Kafka</mark>, and you enjoy working with the
        people who use your tables. Our stack also includes Scala and Snowflake.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Badge className="border-transparent bg-highlight text-highlight-foreground">
          CV ready
        </Badge>
        <span className="text-sm text-muted-foreground">
          Select any text on this page to see the selection color.
        </span>
      </div>
    </div>
  ),
};

/** Every token as a swatch. Status text tokens are in Meaning above. */
export const Palette: Story = {
  render: () => (
    <div className="flex flex-col gap-8">
      {(
        [
          [
            "Canvas and text",
            [
              ["background", "Canvas"],
              ["card", "Cards, dialogs"],
              ["foreground", "All body text"],
              ["muted-foreground", "Secondary text"],
            ],
          ],
          [
            "Accent",
            [
              ["primary", "The action"],
              ["brand", "Orange"],
              ["ring", "Focus ring"],
              ["secondary", "Grey pill"],
              ["accent", "Hover"],
            ],
          ],
          [
            "Lines",
            [
              ["border", "Hairlines, dividers"],
              ["input", "Field borders, 3:1"],
            ],
          ],
          [
            "Meaning",
            [
              ["success", "Progress"],
              ["warning", "Waits for you"],
              ["warning-muted", "Warning banner"],
              ["info", "Information"],
              ["destructive", "Loss, error"],
              ["highlight", "Mark"],
            ],
          ],
        ] as const
      ).map(([group, tokens]) => (
        <section key={group} className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">{group}</h3>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {tokens.map(([token, use]) => (
              <Swatch key={token} token={token} use={use} />
            ))}
          </div>
        </section>
      ))}
    </div>
  ),
};
