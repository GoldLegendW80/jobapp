import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  BotIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  FileTextIcon,
  MailIcon,
  MoreHorizontalIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";

const DOCUMENTS = [
  { title: "Base CV", detail: "Updated on 4 November, two pages" },
  {
    title: "CV for Senior data engineer, Lumen Freight",
    detail: "Written on 5 November from your base CV",
  },
  {
    title: "Cover letter for Analytics engineer, Atelier Brume",
    detail: "Written on 4 November, in French",
  },
  {
    title: "LinkedIn data export",
    detail: "Read on 28 October, 41 facts added to your profile",
  },
];

const meta = {
  title: "Components/Data display/Item",
  component: Item,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "One row of a list: a title, a line of detail, maybe an icon and its actions. Use it for assistant runs, documents, saved answers; use a Table when the person compares values across columns.",
      },
    },
  },
} satisfies Meta<typeof Item>;

export default meta;
type Story = StoryObj<typeof meta>;

/** An assistant run on Assistant activity: what it does, its state, when, and a way in. */
export const Typical: Story = {
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemMedia variant="icon">
        <BotIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>
          Filling an application form
          <Badge variant="secondary">Running</Badge>
        </ItemTitle>
        <ItemDescription>
          Senior data engineer, Lumen Freight. Started today at 14:32.
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="outline" size="sm">
          Watch
        </Button>
      </ItemActions>
    </Item>
  ),
};

/** A title alone, for a list where the name says everything. */
export const TitleOnly: Story = {
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemContent>
        <ItemTitle>Base CV</ItemTitle>
      </ItemContent>
    </Item>
  ),
};

/** Add a description for the one detail that tells two rows apart. */
export const WithDescription: Story = {
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemContent>
        <ItemTitle>Base CV</ItemTitle>
        <ItemDescription>Updated on 4 November, two pages</ItemDescription>
      </ItemContent>
    </Item>
  ),
};

/** Add media when an icon tells the kind of row faster than words. */
export const WithMedia: Story = {
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemMedia variant="icon">
        <FileTextIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Base CV</ItemTitle>
        <ItemDescription>Updated on 4 November, two pages</ItemDescription>
      </ItemContent>
    </Item>
  ),
};

/** Add actions at the end: one visible verb, the rest behind More actions. */
export const WithActions: Story = {
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemMedia variant="icon">
        <FileTextIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Base CV</ItemTitle>
        <ItemDescription>Updated on 4 November, two pages</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="outline" size="sm">
          Edit
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="More actions">
          <MoreHorizontalIcon />
        </Button>
      </ItemActions>
    </Item>
  ),
};

/** Outline for a row that stands alone, muted to set a row back, default inside a bordered group. */
export const Variants: Story = {
  render: () => (
    <div className="flex max-w-xl flex-col gap-3">
      <Item variant="outline">
        <ItemContent>
          <ItemTitle>Outline</ItemTitle>
          <ItemDescription>
            A row that stands alone on the page.
          </ItemDescription>
        </ItemContent>
      </Item>
      <Item variant="muted">
        <ItemContent>
          <ItemTitle>Muted</ItemTitle>
          <ItemDescription>
            A row set back from the rest, such as a finished run.
          </ItemDescription>
        </ItemContent>
      </Item>
      <Item>
        <ItemContent>
          <ItemTitle>Default</ItemTitle>
          <ItemDescription>
            No border of its own, for a row inside a bordered group.
          </ItemDescription>
        </ItemContent>
      </Item>
    </div>
  ),
};

/** sm in dense lists and side panels, default everywhere else. */
export const Sizes: Story = {
  render: () => (
    <div className="flex max-w-xl flex-col gap-3">
      <Item variant="outline">
        <ItemMedia variant="icon">
          <MailIcon />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Gmail</ItemTitle>
          <ItemDescription>
            Connected as camille.martin@example.org
          </ItemDescription>
        </ItemContent>
      </Item>
      <Item variant="outline" size="sm">
        <ItemMedia variant="icon">
          <MailIcon />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Gmail</ItemTitle>
          <ItemDescription>
            Connected as camille.martin@example.org
          </ItemDescription>
        </ItemContent>
      </Item>
    </div>
  ),
};

/** The whole row is the link: asChild on an anchor, with a chevron that says it goes somewhere. */
export const AsLink: Story = {
  render: () => (
    <Item variant="outline" asChild className="max-w-xl">
      <a href="#run-report">
        <ItemMedia variant="icon">
          <BotIcon />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Reading your documents</ItemTitle>
          <ItemDescription>
            Finished yesterday at 18:05. 41 facts proposed for your profile.
          </ItemDescription>
        </ItemContent>
        <ItemActions>
          <ChevronRightIcon className="size-4 text-muted-foreground" />
        </ItemActions>
      </a>
    </Item>
  ),
};

/** A list: an ItemGroup on a Card, rows split by ItemSeparator. */
export const Group: Story = {
  render: () => (
    <Card className="max-w-xl gap-0 py-0">
      <ItemGroup>
        {DOCUMENTS.map((document, index) => (
          <div key={document.title} role="listitem">
            {index > 0 ? <ItemSeparator /> : null}
            <Item>
              <ItemMedia variant="icon">
                <FileTextIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>{document.title}</ItemTitle>
                <ItemDescription>{document.detail}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button variant="outline" size="sm">
                  Open<span className="sr-only"> {document.title}</span>
                </Button>
              </ItemActions>
            </Item>
          </div>
        ))}
      </ItemGroup>
    </Card>
  ),
};

/** A run that failed: the badge says so, the description says why and what to do. */
export const ErrorState: Story = {
  name: "Error",
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemMedia variant="icon" className="text-destructive">
        <CircleAlertIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>
          CV for a posting
          <Badge variant="destructive">Failed</Badge>
        </ItemTitle>
        <ItemDescription>
          Stopped at its time limit. Try again, or raise the limit in Account.
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="outline" size="sm">
          Try again
        </Button>
      </ItemActions>
    </Item>
  ),
};

/** Working: a Spinner in place of the icon while the row's action runs. */
export const Working: Story = {
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemMedia variant="icon">
        <Spinner />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Reading your documents</ItemTitle>
        <ItemDescription>3 of 8 documents read</ItemDescription>
      </ItemContent>
    </Item>
  ),
};

/** Long text: the title wraps, the description stops at two lines. */
export const LongText: Story = {
  render: () => (
    <Item variant="outline" className="max-w-xl">
      <ItemMedia variant="icon">
        <FileTextIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>
          Cover letter for Senior staff machine learning platform engineer,
          Kestrel Health Research and Clinical Data Services
        </ItemTitle>
        <ItemDescription>
          Written on 3 November in French, in a warm tone, from your base CV and
          the posting. It names the three projects of yours that match the
          team&apos;s work on search, and asks for a call in the week of 17
          November.
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="outline" size="sm">
          Open
        </Button>
      </ItemActions>
    </Item>
  ),
};

/** Loading: Skeletons in the shape of a row, one per row expected. */
export const Loading: Story = {
  render: () => (
    <ItemGroup
      className="max-w-xl gap-3"
      role="status"
      aria-label="Loading your documents"
    >
      {[0, 1, 2].map((index) => (
        <Item key={index} variant="outline" aria-hidden="true">
          <Skeleton className="size-8 rounded-sm" />
          <ItemContent>
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-4 w-64 max-w-full" />
          </ItemContent>
          <Skeleton className="h-8 w-16 rounded-full" />
        </Item>
      ))}
    </ItemGroup>
  ),
};
