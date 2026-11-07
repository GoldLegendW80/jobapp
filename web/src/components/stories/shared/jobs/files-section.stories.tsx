import type { Meta, StoryObj } from "@storybook/react-vite";
import { userEvent, within } from "storybook/test";

import { FilesSection } from "@/components/shared/jobs/files-section";
import {
  fakeJobFiles,
  LONG_NAMED_FILE,
  POSTING_FILES,
  WORKED_ON_POSTING,
} from "@/design-system/fixtures/posting-parts";
import { withApp } from "@/design-system/with-app";

const meta = {
  title: "Shared/Jobs/Files section",
  component: FilesSection,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Every file of a posting with its size and origin, a link to open or download it, a PDF preview and a JSON viewer: in the main column of a posting.",
      },
      story: { inline: false, height: "560px" },
    },
  },
  args: { jobId: WORKED_ON_POSTING.id, files: POSTING_FILES },
  beforeEach: () => {
    fakeJobFiles();
  },
} satisfies Meta<typeof FilesSection>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The CV and letter written for the posting, an upload, an imported script and a document. */
export const Typical: Story = { name: "A posting's files" };

/** Nothing attached yet. */
export const NoFiles: Story = {
  name: "No files attached",
  args: { files: [] },
};

/** Preview opens the PDF below its line, fetched with the session. */
export const PreviewAPdf: Story = {
  name: "Preview a PDF",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: /Show preview of cv-lumen-freight\.pdf/,
      }),
    );
  },
};

/** While the PDF comes, the preview says it is loading. */
export const PreviewLoading: Story = {
  name: "A preview loading",
  beforeEach: () => {
    fakeJobFiles("slow");
  },
  play: PreviewAPdf.play,
};

/** A file that is not a PDF is never shown: the preview says why and links to the file. */
export const PreviewFails: Story = {
  name: "A preview that cannot be shown",
  beforeEach: () => {
    fakeJobFiles("notPdf");
  },
  play: PreviewAPdf.play,
};

/** A JSON file opens pretty printed under its line. */
export const ReadAJsonFile: Story = {
  name: "Read a JSON file",
  play: async ({ canvasElement }) => {
    await userEvent.click(
      await within(canvasElement).findByRole("button", {
        name: "Show answers.json",
      }),
    );
  },
};

/** A JSON file the server cannot send says so in its place. */
export const JsonFails: Story = {
  name: "A JSON file that cannot be loaded",
  beforeEach: () => {
    fakeJobFiles("broken");
  },
  play: ReadAJsonFile.play,
};

/** A long file name breaks across lines; the buttons stay whole. */
export const LongName: Story = {
  name: "A long file name",
  args: { files: [LONG_NAMED_FILE, ...POSTING_FILES.slice(2, 3)] },
};

/** On a phone the buttons go under the name. */
export const OnAPhone: Story = {
  name: "On a phone",
  render: (args) => (
    <div className="max-w-sm">
      <FilesSection {...args} />
    </div>
  ),
};
