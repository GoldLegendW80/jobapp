import type { Meta, StoryObj } from "@storybook/react-vite";
import { useMemo } from "react";

import { AddFiles } from "@/components/shared/ingestion/add-files";
import {
  staticUploads,
  UPLOADS_REFUSED,
  UPLOADS_UNDER_WAY,
} from "@/design-system/fixtures/shared-ingestion";
import { useUploads } from "@/features/ingestion/api/use-uploads";
import { fakeDocuments, LIMITS } from "@/test/ingestion";

const meta = {
  title: "Shared/Ingestion/Add files",
  component: AddFiles,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The drop zone and Choose files, with each file's progress or refusal under it: the Documents step of Fill my profile, the welcome wizard and the CV upload panel.",
      },
    },
  },
  args: { limits: LIMITS, uploads: staticUploads() },
} satisfies Meta<typeof AddFiles>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing chosen yet: the zone says what it takes, up to the size limit of the server. */
export const NothingAddedYet: Story = {
  name: "Nothing added yet",
};

/** Files sent one at a time: one the server is storing, one on its way, one waiting its turn. */
export const FilesOnTheirWay: Story = {
  name: "Files on their way",
  args: {
    uploads: staticUploads(UPLOADS_UNDER_WAY, "Sending linkedin-export.zip."),
  },
};

/** A refused file, a dropped folder and a file already there stay until they are dismissed. */
export const FilesNotAdded: Story = {
  name: "Files not added, with the reason",
  args: {
    uploads: staticUploads(
      UPLOADS_REFUSED,
      "team-photo.gif: GIF images cannot be read.",
    ),
  },
};

/** A long file name is cut on one line; its state and Dismiss stay in view. */
export const LongFileName: Story = {
  name: "Long file name",
  args: {
    uploads: staticUploads([
      {
        id: 7,
        name: "camille-martin-curriculum-vitae-senior-data-engineer-lyon-version-for-logistics-companies-november-2026-final.pdf",
        size: 248_000,
        state: "uploading",
        progress: 0.73,
      },
    ]),
  },
};

/** On a phone the zone and the list take the whole width. */
export const OnAPhone: Story = {
  name: "On a phone",
  args: {
    uploads: staticUploads([...UPLOADS_UNDER_WAY, ...UPLOADS_REFUSED]),
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};

function LiveAddFiles() {
  const uploads = useUploads(
    useMemo(
      () => ({ onStored: () => undefined, onCancelled: () => undefined }),
      [],
    ),
  );
  return <AddFiles limits={LIMITS} uploads={uploads} />;
}

/** Choose real files from this computer: the fake api keeps text and PDF files and refuses a GIF. */
export const ChooseFilesAndSendThem: Story = {
  name: "Choose files and send them",
  render: () => <LiveAddFiles />,
  beforeEach: () => {
    fakeDocuments([]);
  },
};
