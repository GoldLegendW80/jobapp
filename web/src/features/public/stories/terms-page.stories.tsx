import type { Meta, StoryObj } from "@storybook/react-vite";

import { AppAt } from "@/design-system/app-at";

const meta = {
  title: "Pages/Public/Terms",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The terms of use in three plain sentences. Route /terms, public, no api call.",
      },
    },
  },
  render: () => <AppAt path="/terms" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** As an invited friend, I want the terms in a few plain sentences, so that I know what I agree to without reading a contract. */
export const ReadTheTerms: Story = {};

/** As an invited friend on my phone, I want the terms in one readable column, so that I can read them anywhere. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
};
