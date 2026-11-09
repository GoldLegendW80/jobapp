import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  ChatSection,
  ChatSheet,
} from "@/components/shared/documents/document-view/chat-section";
import {
  CHAT_MESSAGES,
  fakeOneDocument,
  postingDocument,
} from "@/design-system/fixtures/documents";
import { POSTING_DETAIL } from "@/design-system/fixtures/postings";
import { withApp } from "@/design-system/with-app";
import { fakeVersion } from "@/test/documents";

const CV = postingDocument("cv", POSTING_DETAIL.id, {
  latest: fakeVersion({ number: 2 }),
});

const meta = {
  title: "Shared/Documents/Chat section",
  component: ChatSection,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Where the document view puts its chat: Ask for a change, a section of the side rail; on a phone a button of the page header that opens it in a drawer from the bottom.",
      },
    },
  },
  args: { document: CV, generation: null },
  beforeEach: () => {
    fakeOneDocument(CV, {
      messages: CHAT_MESSAGES,
    });
  },
} satisfies Meta<typeof ChatSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The section of the side rail. */
export const InTheRail: Story = {
  name: "In the side rail",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};

/** On a phone: the header's button opens the chat in a drawer. */
export const InADrawer: Story = {
  name: "In a drawer on a phone",
  render: (args) => <ChatSheet {...args} />,
};
