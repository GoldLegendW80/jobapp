import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  EyeIcon,
  FileTextIcon,
  MessageSquareIcon,
  PencilIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const CV_TEXT =
  "Camille Martin, senior data engineer. Eight years building data pipelines for logistics and retail, most recently at Orchard Rail, where the team moved 40 depots onto one warehouse.";

const meta = {
  title: "Components/Data display/Tabs",
  component: Tabs,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Switches between views of one subject in place: see, edit or chat about a document; the sections of the profile. Each panel stands alone. When a view needs its own address, link to a page instead.",
      },
    },
  },
} satisfies Meta<typeof Tabs>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The views of a document: the one the person came for is selected first. */
export const Typical: Story = {
  render: () => (
    <Tabs defaultValue="see" className="max-w-xl">
      <TabsList aria-label="Views of this document">
        <TabsTrigger value="see">See</TabsTrigger>
        <TabsTrigger value="edit">Edit</TabsTrigger>
        <TabsTrigger value="chat">Chat</TabsTrigger>
      </TabsList>
      <TabsContent value="see" className="text-sm">
        {CV_TEXT}
      </TabsContent>
      <TabsContent value="edit" className="text-sm">
        Edit the text of your CV here.
      </TabsContent>
      <TabsContent value="chat" className="text-sm">
        Ask the assistant to change your CV.
      </TabsContent>
    </Tabs>
  ),
};

/** An icon before each label, when the icons are familiar enough to help scanning. */
export const WithIcons: Story = {
  render: () => (
    <Tabs defaultValue="see" className="max-w-xl">
      <TabsList aria-label="Views of this document">
        <TabsTrigger value="see">
          <EyeIcon />
          See
        </TabsTrigger>
        <TabsTrigger value="edit">
          <PencilIcon />
          Edit
        </TabsTrigger>
        <TabsTrigger value="chat">
          <MessageSquareIcon />
          Chat
        </TabsTrigger>
      </TabsList>
      <TabsContent value="see" className="text-sm">
        {CV_TEXT}
      </TabsContent>
    </Tabs>
  ),
};

/** A count after the label says how much waits in each panel. */
export const WithCount: Story = {
  render: () => (
    <Tabs defaultValue="ready" className="max-w-xl">
      <TabsList aria-label="Applications">
        <TabsTrigger value="ready">
          Ready
          <Badge variant="secondary">3</Badge>
        </TabsTrigger>
        <TabsTrigger value="sent">
          Sent
          <Badge variant="secondary">12</Badge>
        </TabsTrigger>
        <TabsTrigger value="stopped">
          Stopped
          <Badge variant="secondary">1</Badge>
        </TabsTrigger>
      </TabsList>
      <TabsContent value="ready" className="text-sm">
        Three forms are filled and wait for your approval.
      </TabsContent>
    </Tabs>
  ),
};

/** A view that is not available yet is disabled, and the panel next to it says why. */
export const Disabled: Story = {
  render: () => (
    <Tabs defaultValue="see" className="max-w-xl">
      <TabsList aria-label="Views of this document">
        <TabsTrigger value="see">See</TabsTrigger>
        <TabsTrigger value="edit">Edit</TabsTrigger>
        <TabsTrigger value="compare" disabled>
          Compare
        </TabsTrigger>
      </TabsList>
      <TabsContent value="see" className="text-sm">
        Compare opens once this CV has a second version.
      </TabsContent>
    </Tabs>
  ),
};

/** An empty panel invites the action that fills it. */
export const EmptyPanel: Story = {
  render: () => (
    <Tabs defaultValue="letter" className="max-w-xl">
      <TabsList aria-label="Documents for this posting">
        <TabsTrigger value="cv">CV</TabsTrigger>
        <TabsTrigger value="letter">Cover letter</TabsTrigger>
      </TabsList>
      <TabsContent value="letter">
        <Empty className="border">
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
      </TabsContent>
    </Tabs>
  ),
};

/** Loading: the tabs show at once, a Skeleton holds the panel in the shape of its text. */
export const Loading: Story = {
  render: () => (
    <Tabs defaultValue="see" className="max-w-xl">
      <TabsList aria-label="Views of this document">
        <TabsTrigger value="see">See</TabsTrigger>
        <TabsTrigger value="edit">Edit</TabsTrigger>
        <TabsTrigger value="chat">Chat</TabsTrigger>
      </TabsList>
      <TabsContent value="see">
        <div
          role="status"
          aria-label="Loading the CV"
          className="flex flex-col gap-2"
        >
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      </TabsContent>
    </Tabs>
  ),
};

/** Many or long labels: on a phone the list scrolls sideways rather than squeezing the labels. */
export const ManyTabs: Story = {
  render: () => (
    <Tabs defaultValue="facts" className="max-w-xl">
      <TabsList
        aria-label="Profile sections"
        className="justify-start overflow-x-auto"
      >
        <TabsTrigger value="facts">Facts</TabsTrigger>
        <TabsTrigger value="skills">Skills</TabsTrigger>
        <TabsTrigger value="roles">Roles and experience</TabsTrigger>
        <TabsTrigger value="search">What you are looking for</TabsTrigger>
        <TabsTrigger value="answers">Saved answers</TabsTrigger>
        <TabsTrigger value="history">History</TabsTrigger>
      </TabsList>
      <TabsContent value="facts" className="text-sm">
        41 facts about you, read from your documents and checked by you.
      </TabsContent>
    </Tabs>
  ),
};
