import type { Meta, StoryObj } from "@storybook/react-vite";
import { BotIcon, FileTextIcon, InboxIcon, SearchXIcon } from "lucide-react";

import { WORDING } from "@/lib/wording";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";

const meta = {
  title: "Components/Feedback/Empty",
  component: Empty,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Fills a place that has nothing to show yet and invites the next step: an empty deck, no documents, no runs. Say why it is empty in one sentence and offer the one action that fills it.",
      },
    },
  },
} satisfies Meta<typeof Empty>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The deck before the first search: why it is empty, and the action that fills it. */
export const Typical: Story = {
  render: () => (
    <Empty className="border">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <InboxIcon />
        </EmptyMedia>
        <EmptyTitle>{WORDING.deckNoPostings}</EmptyTitle>
        <EmptyDescription>{WORDING.deckNoPostingsHint}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button>{WORDING.findJobs}</Button>
      </EmptyContent>
    </Empty>
  ),
};

/** A title alone, inside a small region where the next step is obvious. */
export const TitleOnly: Story = {
  render: () => (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>No saved answers yet</EmptyTitle>
      </EmptyHeader>
    </Empty>
  ),
};

/** Add a description that says what will fill the place. */
export const WithDescription: Story = {
  render: () => (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>No saved answers yet</EmptyTitle>
        <EmptyDescription>
          When you answer a question on an application form, you can keep the
          answer here for the next form that asks it.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  ),
};

/** Add an icon in its tile to say at a glance what kind of thing is missing. */
export const WithIcon: Story = {
  render: () => (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <BotIcon />
        </EmptyMedia>
        <EmptyTitle>No assistant activity yet</EmptyTitle>
        <EmptyDescription>
          Each time the assistant reads, writes or applies for you, the run
          shows up here with what it did.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  ),
};

/** Add the action: one primary button, a second outline one only when there are two real ways in. */
export const WithActions: Story = {
  render: () => (
    <Empty className="border">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <FileTextIcon />
        </EmptyMedia>
        <EmptyTitle>No CV yet</EmptyTitle>
        <EmptyDescription>
          Add the CV you have, or let the app write a base CV from your profile.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="flex-row flex-wrap justify-center">
        <Button>Add your CV</Button>
        <Button variant="outline">Write a base CV</Button>
      </EmptyContent>
    </Empty>
  ),
};

/** No results for the current filters: say so, and offer to clear them. */
export const NoResults: Story = {
  render: () => (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <SearchXIcon />
        </EmptyMedia>
        <EmptyTitle>No postings match these filters</EmptyTitle>
        <EmptyDescription>
          Remote only, permanent, from 60,000 EUR a year. Loosen one of them to
          see more.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button variant="outline">Clear the filters</Button>
      </EmptyContent>
    </Empty>
  ),
};

/** A long description is kept to a readable width and balanced across its lines. */
export const LongText: Story = {
  render: () => (
    <Empty className="border">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <InboxIcon />
        </EmptyMedia>
        <EmptyTitle>Nothing new above your threshold of 70</EmptyTitle>
        <EmptyDescription>
          Your profile has no skills yet, so new postings score close to 0 and
          stay under your threshold. Add the skills you have in your profile, or
          let Fill my profile read your CV. Then choose Rescore all postings in
          the menu of the Jobs page.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button>{WORDING.addSkills}</Button>
      </EmptyContent>
    </Empty>
  ),
};
