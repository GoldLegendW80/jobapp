import type { Meta, StoryObj } from "@storybook/react-vite";

import { UploadPanel } from "@/components/shared/documents/upload-panel";
import { withApp } from "@/design-system/with-app";
import { fakeDocuments } from "@/test/documents";

const meta = {
  title: "Shared/Documents/Upload panel",
  component: UploadPanel,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Upload a CV and turn it into a base CV to edit, after the consent of what is sent; then complete the profile from it. On the CV tab of the profile and in an onboarding step.",
      },
    },
  },
  args: { openHref: (id: string) => `/profile/cv/${id}` },
  beforeEach: () => {
    fakeDocuments();
  },
} satisfies Meta<typeof UploadPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Drop a PDF or a Word file, or choose one. */
export const ChooseAFile: Story = { name: "Choose a file" };

/** Inside a section that already says Upload your CV. */
export const Untitled: Story = {
  name: "Inside a titled section",
  args: { titled: false },
};

/** At a phone's width. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
