import type { Meta, StoryObj } from "@storybook/react-vite";

import { ListedMention } from "@/components/shared/jobs/listed-mention";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Seen in a list",
  component: ListedMention,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Jobs J31: on the row of the Jobs list and on the posting, a posting kept from a card of a list says so, with a link to the list and one to its own page. Nothing for any other posting.",
      },
    },
  },
  args: {
    job: {
      list_url: "https://aggregator.example.org/jobs?q=genai",
      url: "https://jobs.example.org/genai-engineer",
    },
  },
} satisfies Meta<typeof ListedMention>;
export default meta;
type Story = StoryObj<typeof meta>;

/** A posting seen only in a list, both addresses known. */
export const SeenInAList: Story = {};

/** A card whose own address is not one the app opens: only the list is a link. */
export const OnlyTheList: Story = {
  args: {
    job: {
      list_url: "https://aggregator.example.org/jobs?q=genai",
      url: null,
    },
  },
};

/** An ordinary posting, its page read: nothing shows. */
export const PageRead: Story = {
  args: { job: { list_url: null, url: "https://jobs.example.org/genai" } },
};
