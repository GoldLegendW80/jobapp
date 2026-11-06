import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";

const meta = {
  title: "Pages/Public/Privacy",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "What the app reads, keeps and deletes: Gmail, applications, the assistant and the app's browser. Route /privacy, public, no api call.",
      },
    },
  },
  render: () => <AppAt path="/privacy" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an invited friend, I want to read what the app does with my mail and my browser before I connect them, so that I decide knowing what is kept and for how long. */
export const ReadHowMyDataIsUsed: Story = {};

/** As an invited friend on my phone, I want the privacy text in one readable column, so that I can read it from the link in an e-mail. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
};
