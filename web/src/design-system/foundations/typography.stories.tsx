import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

const meta = {
  title: "Foundations/Typography",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One family, Instrument Sans: a precise, slightly narrow grotesk, so long job titles and dense lists keep their line. Headings are medium (500), never bold: hierarchy comes from size first, then weight, never from a second typeface, color or capitals. Numbers that are compared (fit, pay, counts, times) use tabular figures; this face keeps commas, points and colons narrow when it does.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const ROLES = [
  {
    role: "Display",
    classes: "text-4xl font-medium tracking-tight",
    spec: "36 / 40, 500, tight",
    use: "The one message of an empty page or the sign in page",
    sample: "Find the jobs that fit you",
  },
  {
    role: "Page title",
    classes: "text-3xl font-medium tracking-tight",
    spec: "30 / 36, 500, tight",
    use: "The h1 of every page, through PageHeader",
    sample: "Senior data engineer at Lumen Freight",
  },
  {
    role: "Section title",
    classes: "text-lg font-medium tracking-tight",
    spec: "18 / 28, 500, tight",
    use: "The h2 of a page section",
    sample: "Your application",
  },
  {
    role: "Card title",
    classes: "text-base font-semibold leading-none",
    spec: "16 / 16, 600",
    use: "CardTitle, dialog titles",
    sample: "Cover letter",
  },
  {
    role: "Reading",
    classes: "text-base leading-relaxed",
    spec: "16 / 26, 400",
    use: "Posting descriptions, letters, the profile in Markdown",
    sample: "You will own the pricing pipelines end to end.",
  },
  {
    role: "Body",
    classes: "text-sm",
    spec: "14 / 20, 400",
    use: "The default of the interface",
    sample: "Lyon, hybrid, posted 3 days ago",
  },
  {
    role: "Label",
    classes: "text-sm font-medium",
    spec: "14 / 20, 500",
    use: "Field labels, buttons, table headers",
    sample: "Desired salary",
  },
  {
    role: "Caption",
    classes: "text-xs text-muted-foreground",
    spec: "12 / 16, 400, muted",
    use: "Meta, help under a field, timestamps",
    sample: "Read from your CV on 4 November",
  },
] as const;

/** One row per role, smallest classes that produce it. Headings balance their lines by themselves (index.css). */
export const Scale: Story = {
  render: () => (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Role</TableHead>
          <TableHead>Classes</TableHead>
          <TableHead>Size, line, weight</TableHead>
          <TableHead>Specimen</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ROLES.map((row) => (
          <TableRow key={row.role}>
            <TableCell label="Role" className="align-top">
              <div className="flex flex-col gap-0.5">
                <span className="font-medium">{row.role}</span>
                <span className="text-xs whitespace-normal text-muted-foreground">
                  {row.use}
                </span>
              </div>
            </TableCell>
            <TableCell label="Classes" className="align-top">
              <code className="text-xs whitespace-normal">{row.classes}</code>
            </TableCell>
            <TableCell
              label="Size, line, weight"
              className="align-top text-xs text-muted-foreground tabular-nums"
            >
              {row.spec}
            </TableCell>
            <TableCell label="Specimen" className="max-w-md align-top">
              <p className={cn("truncate", row.classes)}>{row.sample}</p>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ),
};

/** Running text at reading size: the measure stays under 75 characters, links are underlined. */
export const RunningText: Story = {
  render: () => (
    <article className="flex max-w-prose flex-col gap-4">
      <h1 className="text-3xl font-medium tracking-tight">
        Senior data engineer at Lumen Freight
      </h1>
      <p className="text-sm text-muted-foreground">
        Lyon, hybrid, permanent, 55,000 to 65,000 EUR a year
      </p>
      <p className="text-base leading-relaxed">
        Lumen Freight moves 40,000 pallets a day across Europe. The pricing team
        needs someone to own its pipelines, from the raw tracking events to the
        tables our planners read every morning. You will work with{" "}
        <code className="rounded-sm bg-muted px-1 py-0.5 text-sm">dbt</code>,
        Kafka and Python, and you will{" "}
        <a
          href="#running-text"
          className="text-primary underline underline-offset-4"
        >
          meet the team
        </a>{" "}
        before your first day.
      </p>
    </article>
  ),
};

/** Tabular figures keep columns of scores and pay aligned; proportional figures are for prose. */
export const Numbers: Story = {
  render: () => (
    <div className="grid max-w-md grid-cols-2 gap-8 text-sm">
      {(["proportional-nums", "tabular-nums"] as const).map((variant) => (
        <div key={variant} className="flex flex-col gap-1">
          <code className="text-xs text-muted-foreground">{variant}</code>
          {["Fit 81", "Fit 17", "111,000 EUR", "48,450 EUR"].map((value) => (
            <span key={value} className={cn("text-right", variant)}>
              {value}
            </span>
          ))}
        </div>
      ))}
    </div>
  ),
};
