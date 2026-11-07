import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { ShotView } from "@/components/shared/apply/shot-view";
import { fakeFormShots } from "@/design-system/fixtures/applications";
import { APP_ID } from "@/test/apply";
import { server } from "@/test/server";

const meta = {
  title: "Shared/Apply/Screenshot",
  component: ShotView,
  tags: ["autodocs"],
  parameters: {
    docs: {
      story: { inline: false, height: "560px" },
      description: {
        component:
          "One screenshot of an application's form, small on the page and full size in a dialog when clicked: the form before the click and the page after it, in Your application and on the review queue's cards.",
      },
    },
  },
  args: {
    applicationId: APP_ID,
    shotId: "shot-before",
    caption: "The form before the click",
  },
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
  beforeEach: () => {
    fakeFormShots();
  },
} satisfies Meta<typeof ShotView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The filled form as the assistant saw it, its Submit button outlined. */
export const TheFormBeforeTheClick: Story = {
  name: "The form before the click",
};

/** The page after the click, the second screenshot of a sent application. */
export const ThePageAfterTheClick: Story = {
  name: "The page after the click",
  args: { shotId: "shot-after", caption: "The page after the click" },
};

/** Clicked: the whole page, full size, in a dialog. */
export const Enlarged: Story = {
  name: "Enlarged",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Enlarge: The form before the click",
      }),
    );
  },
};

/** The image could not be read: its caption stays, the frame shows the alternative text. */
export const ImageMissing: Story = {
  name: "The image could not be loaded",
  beforeEach: () => {
    server.use(
      http.get(
        "*/api/applications/:id/screenshots/:shot",
        () => new HttpResponse(null, { status: 404 }),
      ),
    );
  },
};
