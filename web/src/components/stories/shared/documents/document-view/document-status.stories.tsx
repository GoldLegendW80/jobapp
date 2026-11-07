import type { Meta, StoryObj } from "@storybook/react-vite";

import { DocumentStatus } from "@/components/shared/documents/document-view/document-status";

const meta = {
  title: "Shared/Documents/Document status",
  component: DocumentStatus,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "What a document is doing, at the top of the document view: waiting to start and why, being written, failed, its page being drawn, or a page that could not be drawn. Nothing when all is done.",
      },
    },
  },
  args: {
    open: false,
    rendering: false,
    generation: null,
    renderMessage: null,
    kind: "cv",
    hasVersion: true,
  },
} satisfies Meta<typeof DocumentStatus>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The writer is working on a new version. */
export const Writing: Story = {
  name: "Writing a new version",
  args: { open: true, generation: { status: "running", wait_reason: null } },
};

/** The first CV, built from the profile. */
export const FirstCv: Story = {
  name: "Building the first CV",
  args: {
    open: true,
    hasVersion: false,
    generation: { status: "running", wait_reason: null },
  },
};

/** Waiting, and what it waits for. */
export const Waiting: Story = {
  name: "Waiting for a model credential",
  args: {
    open: true,
    generation: { status: "waiting", wait_reason: "credential" },
  },
};

/** The last attempt failed, in words. */
export const Failed: Story = {
  name: "The last attempt failed",
  args: {
    generation: {
      id: "g-1",
      status: "failed",
      wait_reason: null,
      error: "token_limit",
    },
  },
};

/** The page is being drawn. */
export const Drawing: Story = {
  name: "Drawing the page",
  args: { rendering: true },
};

/** The page could not be drawn: the renderer's own sentence. */
export const NotDrawn: Story = {
  name: "The page could not be drawn",
  args: { renderMessage: "The page took too long to draw. Try again." },
};
