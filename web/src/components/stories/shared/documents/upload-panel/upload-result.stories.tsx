import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { UploadResult } from "@/components/shared/documents/upload-panel/upload-result";
import { withApp } from "@/design-system/with-app";
import { fakeCv } from "@/test/documents";

const meta = {
  title: "Shared/Documents/Upload result",
  component: UploadResult,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The upload panel once the CV is converted: open it, make it the default CV, and complete the profile from it.",
      },
    },
  },
  args: {
    cv: fakeCv({
      id: "cv-up",
      name: "camille-martin-cv",
      source: "uploaded",
      is_default: false,
    }),
    openHref: (id: string) => `/profile/cv/${id}`,
    defaulting: false,
    onDefault: fn(),
    complete: null,
  },
} satisfies Meta<typeof UploadResult>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Converted: open it or make it the default. */
export const Converted: Story = {};

/** Already the default CV. */
export const AlreadyDefault: Story = {
  name: "Already the default",
  args: {
    cv: fakeCv({ id: "cv-up", name: "camille-martin-cv", source: "uploaded" }),
  },
};

/** While it becomes the default. */
export const MakingDefault: Story = {
  name: "Becoming the default",
  args: { defaulting: true },
};
