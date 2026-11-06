import type { Meta, StoryObj } from "@storybook/react-vite";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
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
import { Skeleton } from "@/components/ui/skeleton";

function PostingCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Senior data engineer</CardTitle>
        <CardDescription>Lumen Freight, Lyon, Hybrid</CardDescription>
        <CardAction>
          <Badge variant="secondary">Fit 86</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="text-sm">55,000 to 65,000 EUR a year</CardContent>
      <CardFooter className="gap-2">
        <Button>Apply</Button>
        <Button variant="outline">Pass</Button>
      </CardFooter>
    </Card>
  );
}

function PostingCardSkeleton() {
  return (
    <Card role="status" aria-label="Loading the posting">
      <CardHeader>
        <Skeleton className="h-4 w-44" />
        <Skeleton className="h-4 w-36" />
        <CardAction>
          <Skeleton className="h-5 w-14 rounded-full" />
        </CardAction>
      </CardHeader>
      <CardContent>
        <Skeleton className="h-4 w-48" />
      </CardContent>
      <CardFooter className="gap-2">
        <Skeleton className="h-10 w-24 rounded-full" />
        <Skeleton className="h-10 w-20 rounded-full" />
      </CardFooter>
    </Card>
  );
}

const meta = {
  title: "Components/Feedback/Skeleton",
  component: Skeleton,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Holds the place of content whose shape is known while it loads: a posting card, a table row, a profile header. Match the final layout's sizes so nothing moves when the content arrives, and name the wait once with role status on the wrapper.",
      },
    },
  },
} satisfies Meta<typeof Skeleton>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A posting card loading: one Skeleton per line of text and per button, at their real sizes. */
export const Typical: Story = {
  render: () => (
    <div className="max-w-md">
      <PostingCardSkeleton />
    </div>
  ),
};

/** Loading and loaded side by side: the shapes line up, so the swap does not move the page. */
export const BesideLoaded: Story = {
  render: () => (
    <div className="grid max-w-3xl gap-4 sm:grid-cols-2">
      <PostingCardSkeleton />
      <PostingCard />
    </div>
  ),
};

/** Lines of text: the last line shorter, as a paragraph ends. */
export const Text: Story = {
  render: () => (
    <div
      role="status"
      aria-label="Loading the cover letter"
      className="flex max-w-md flex-col gap-2"
    >
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/5" />
    </div>
  ),
};

/** A person: a round Skeleton for the avatar, two lines for the name and e-mail. */
export const ProfileHeader: Story = {
  render: () => (
    <div className="flex flex-col gap-6">
      <div
        role="status"
        aria-label="Loading the account"
        className="flex items-center gap-3"
      >
        <Skeleton className="size-10 rounded-full" />
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-3 w-44" />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Avatar size="lg" aria-hidden="true">
          <AvatarFallback>CM</AvatarFallback>
        </Avatar>
        <div className="flex flex-col">
          <span className="text-sm font-medium">Camille Martin</span>
          <span className="text-xs text-muted-foreground">
            camille.martin@example.org
          </span>
        </div>
      </div>
    </div>
  ),
};

/** Many: a list of rows loading, one per row expected on the first page. */
export const ManyRows: Story = {
  render: () => (
    <div
      role="status"
      aria-label="Loading postings"
      className="flex max-w-2xl flex-col rounded-lg border bg-card"
    >
      {[0, 1, 2, 3, 4, 5].map((index) => (
        <div
          key={index}
          className="flex h-12 items-center gap-3 border-b px-3 last:border-b-0"
        >
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 flex-1" />
          <Skeleton className="h-3 w-28 max-sm:hidden" />
          <Skeleton className="h-4 w-14 rounded-full" />
        </div>
      ))}
    </div>
  ),
};
