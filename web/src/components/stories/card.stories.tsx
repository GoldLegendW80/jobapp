import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  CircleAlertIcon,
  FileTextIcon,
  MoreHorizontalIcon,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";

const POSTINGS = [
  {
    title: "Senior data engineer",
    where: "Lumen Freight, Lyon, Hybrid",
    pay: "55,000 to 65,000 EUR a year",
    fit: 86,
  },
  {
    title: "Analytics engineer",
    where: "Atelier Brume, Paris, Remote",
    pay: "48,000 to 56,000 EUR a year",
    fit: 78,
  },
  {
    title: "Data platform lead",
    where: "Kestrel Health, Nantes, On site",
    pay: "70,000 to 80,000 EUR a year",
    fit: 71,
  },
  {
    title: "Machine learning engineer",
    where: "Orchard Rail, Bordeaux, Hybrid",
    pay: "Pay not given",
    fit: 64,
  },
  {
    title: "Backend engineer, Python",
    where: "Vireo Energy, Lille, Remote",
    pay: "50,000 to 58,000 EUR a year",
    fit: 59,
  },
  {
    title: "Data engineer, freelance mission",
    where: "Tidewater Studio, Remote",
    pay: "550 EUR a day",
    fit: 52,
  },
];

const meta = {
  title: "Components/Data display/Card",
  component: Card,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A surface for one thing the person reads or acts on: a posting, a document, a section of settings. Flat, drawn with a hairline; the header names it, the footer holds its actions.",
      },
    },
  },
} satisfies Meta<typeof Card>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting as Discover shows it: what, where, pay, the skills it matches, and the two choices. */
export const Typical: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Senior data engineer</CardTitle>
        <CardDescription>Lumen Freight, Lyon, Hybrid</CardDescription>
        <CardAction>
          <Badge variant="secondary" className="tabular-nums">
            Fit 86
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        <p>55,000 to 65,000 EUR a year, permanent.</p>
        <p className="text-muted-foreground">
          You match 7 of its 9 skills: <mark>Python</mark>, <mark>Airflow</mark>
          , <mark>dbt</mark> and 4 more.
        </p>
      </CardContent>
      <CardFooter className="gap-2">
        <Button>Apply</Button>
        <Button variant="outline">Pass</Button>
      </CardFooter>
    </Card>
  ),
};

/** Header only: a title and one line of context, for a card that is a link or a summary. */
export const Header: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Base CV</CardTitle>
        <CardDescription>
          Updated on 4 November. Every CV for a posting starts from it.
        </CardDescription>
      </CardHeader>
    </Card>
  ),
};

/** Add content when the card carries more than its title says. */
export const WithContent: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Base CV</CardTitle>
        <CardDescription>
          Updated on 4 November. Every CV for a posting starts from it.
        </CardDescription>
      </CardHeader>
      <CardContent className="text-sm">
        Two pages, in English. Eight roles, from 2014 to today, and 23 skills.
      </CardContent>
    </Card>
  ),
};

/** Add a footer for the actions of the card, the primary one first. */
export const WithFooter: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Base CV</CardTitle>
        <CardDescription>
          Updated on 4 November. Every CV for a posting starts from it.
        </CardDescription>
      </CardHeader>
      <CardContent className="text-sm">
        Two pages, in English. Eight roles, from 2014 to today, and 23 skills.
      </CardContent>
      <CardFooter className="gap-2">
        <Button>Edit</Button>
        <Button variant="outline">Download PDF</Button>
      </CardFooter>
    </Card>
  ),
};

/** CardAction sits at the top right of the header: a status, or a menu of less frequent actions. */
export const WithHeaderAction: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Base CV</CardTitle>
        <CardDescription>
          Updated on 4 November. Every CV for a posting starts from it.
        </CardDescription>
        <CardAction>
          <Button variant="ghost" size="icon-sm" aria-label="More actions">
            <MoreHorizontalIcon />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="text-sm">
        Two pages, in English. Eight roles, from 2014 to today, and 23 skills.
      </CardContent>
      <CardFooter className="gap-2">
        <Button>Edit</Button>
        <Button variant="outline">Download PDF</Button>
      </CardFooter>
    </Card>
  ),
};

/** A section of settings: a border under the header and above the footer sets the Save apart. */
export const SettingsSection: Story = {
  render: () => (
    <Card className="max-w-lg">
      <CardHeader className="border-b">
        <CardTitle>Fit score</CardTitle>
        <CardDescription>
          How closely a posting must match your profile before the app acts on
          it.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-1 text-sm">
        <p>Show postings from 50.</p>
        <p>Write documents from 70.</p>
        <p>Apply automatically from 85.</p>
      </CardContent>
      <CardFooter className="justify-end border-t">
        <Button>Save</Button>
      </CardFooter>
    </Card>
  ),
};

/** A long title wraps beside the header action, which keeps its corner. */
export const LongText: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>
          Senior staff machine learning platform engineer, recommendations and
          search infrastructure
        </CardTitle>
        <CardDescription>
          Kestrel Health Research and Clinical Data Services, Saint-Herblain,
          near Nantes, Hybrid with two days a week on site
        </CardDescription>
        <CardAction>
          <Badge variant="secondary" className="tabular-nums">
            Fit 71
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="text-sm">
        70,000 to 80,000 EUR a year, permanent, with a yearly bonus of up to 10
        percent and a budget for conferences and training.
      </CardContent>
      <CardFooter className="flex-wrap gap-2">
        <Button>Apply</Button>
        <Button variant="outline">Pass</Button>
      </CardFooter>
    </Card>
  ),
};

/** Nothing to show yet: an Empty inside the card invites the one action that fills it. */
export const EmptyState: Story = {
  name: "Empty",
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Cover letter</CardTitle>
        <CardDescription>Senior data engineer, Lumen Freight</CardDescription>
      </CardHeader>
      <CardContent>
        <Empty className="p-0 md:p-0">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FileTextIcon />
            </EmptyMedia>
            <EmptyTitle>No cover letter yet</EmptyTitle>
            <EmptyDescription>
              The app writes one from your base CV and this posting.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button>Write a cover letter</Button>
          </EmptyContent>
        </Empty>
      </CardContent>
    </Card>
  ),
};

/** A failed step: an Alert says what happened and what to do, the footer offers to try again. */
export const ErrorState: Story = {
  name: "Error",
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>CV for this posting</CardTitle>
        <CardDescription>Senior data engineer, Lumen Freight</CardDescription>
      </CardHeader>
      <CardContent>
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>The CV could not be written</AlertTitle>
          <AlertDescription>
            The run reached its token budget and was stopped. Raise the budget
            in Account, then try again.
          </AlertDescription>
        </Alert>
      </CardContent>
      <CardFooter>
        <Button variant="outline">Try again</Button>
      </CardFooter>
    </Card>
  ),
};

/** Loading: Skeletons in the shape of the posting card, so nothing moves when it arrives. */
export const Loading: Story = {
  render: () => (
    <Card className="max-w-md" role="status" aria-label="Loading the posting">
      <CardHeader>
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-40" />
        <CardAction>
          <Skeleton className="h-5 w-14 rounded-full" />
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-4 w-full" />
      </CardContent>
      <CardFooter className="gap-2">
        <Skeleton className="h-10 w-24 rounded-full" />
        <Skeleton className="h-10 w-20 rounded-full" />
      </CardFooter>
    </Card>
  ),
};

/** Many cards: a grid that drops to one column on a phone. */
export const Many: Story = {
  render: () => (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {POSTINGS.map((posting) => (
        <Card key={posting.title}>
          <CardHeader>
            <CardTitle>{posting.title}</CardTitle>
            <CardDescription>{posting.where}</CardDescription>
            <CardAction>
              <Badge variant="secondary" className="tabular-nums">
                Fit {posting.fit}
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent className="text-sm">{posting.pay}</CardContent>
          <CardFooter className="mt-auto gap-2">
            <Button size="sm">Apply</Button>
            <Button size="sm" variant="outline">
              Pass
            </Button>
          </CardFooter>
        </Card>
      ))}
    </div>
  ),
};
