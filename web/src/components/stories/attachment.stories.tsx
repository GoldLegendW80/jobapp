import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  DownloadIcon,
  EyeIcon,
  FileImageIcon,
  FileTextIcon,
  RotateCcwIcon,
  XIcon,
} from "lucide-react";

import {
  Attachment,
  AttachmentAction,
  AttachmentActions,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment";
import { Spinner } from "@/components/ui/spinner";

type State = "idle" | "uploading" | "processing" | "error" | "done";

const STATES: { state: State; title: string; description: string }[] = [
  {
    state: "idle",
    title: "Reference letter.pdf",
    description: "PDF, 180 KB, waiting",
  },
  {
    state: "uploading",
    title: "Base CV.pdf",
    description: "PDF, 240 KB, 42%",
  },
  {
    state: "processing",
    title: "LinkedIn data export.zip",
    description: "Archive, 1.2 MB, reading",
  },
  {
    state: "error",
    title: "Scan of a diploma.heic",
    description: "This kind of file cannot be read. Add it as a PDF or a JPEG.",
  },
  {
    state: "done",
    title: "Cover letter, Atelier Brume.pdf",
    description: "PDF, 96 KB",
  },
];

function FileRow({
  state,
  title,
  description,
}: {
  state: State;
  title: string;
  description: string;
}) {
  const moving = state === "uploading" || state === "processing";
  return (
    <Attachment state={state} className="w-80">
      <AttachmentMedia>
        {moving ? (
          <Spinner aria-hidden="true" role={undefined} />
        ) : (
          <FileTextIcon aria-hidden="true" />
        )}
      </AttachmentMedia>
      <AttachmentContent>
        <AttachmentTitle>{title}</AttachmentTitle>
        <AttachmentDescription>{description}</AttachmentDescription>
      </AttachmentContent>
      <AttachmentActions>
        {state === "error" ? (
          <AttachmentAction aria-label={`Try ${title} again`}>
            <RotateCcwIcon />
          </AttachmentAction>
        ) : null}
        <AttachmentAction
          aria-label={`${moving ? "Cancel" : "Remove"} ${title}`}
        >
          <XIcon />
        </AttachmentAction>
      </AttachmentActions>
    </Attachment>
  );
}

const meta = {
  title: "Components/Data display/Attachment",
  component: Attachment,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A file the person added or the app keeps: a document being sent for Fill my profile, the files of a posting, a file in a conversation. Its state says where it is (waiting, sending, being read, failed, done); its actions sit at its end, each named for the file.",
      },
    },
  },
} satisfies Meta<typeof Attachment>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A file of a posting: its kind, its name, its size, and Download. */
export const Typical: Story = {
  render: () => (
    <Attachment className="w-80">
      <AttachmentMedia>
        <FileTextIcon aria-hidden="true" />
      </AttachmentMedia>
      <AttachmentContent>
        <AttachmentTitle>Posting, Lumen Freight.pdf</AttachmentTitle>
        <AttachmentDescription>
          PDF, 312 KB, saved by the app
        </AttachmentDescription>
      </AttachmentContent>
      <AttachmentActions>
        <AttachmentAction aria-label="Preview Posting, Lumen Freight.pdf">
          <EyeIcon />
        </AttachmentAction>
        <AttachmentAction aria-label="Download Posting, Lumen Freight.pdf">
          <DownloadIcon />
        </AttachmentAction>
      </AttachmentActions>
    </Attachment>
  ),
};

/** Every state, in the order a file goes through them. The title shimmers while it moves. */
export const States: Story = {
  render: () => (
    <div className="flex flex-col gap-3">
      {STATES.map((file) => (
        <FileRow key={file.state} {...file} />
      ))}
    </div>
  ),
};

/** default in lists, sm in a dense list, xs as a chip in a conversation's prompt. */
export const Sizes: Story = {
  render: () => (
    <div className="flex flex-col items-start gap-3">
      {(["default", "sm", "xs"] as const).map((size) => (
        <Attachment key={size} size={size}>
          <AttachmentMedia>
            <FileTextIcon aria-hidden="true" />
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle>Base CV.pdf</AttachmentTitle>
            <AttachmentDescription>PDF, 240 KB</AttachmentDescription>
          </AttachmentContent>
        </Attachment>
      ))}
    </div>
  ),
};

/** Vertical, with a picture: a scanned page or a photo, in a row that scrolls sideways. */
export const ImagesInARow: Story = {
  render: () => (
    <AttachmentGroup className="max-w-md">
      {["Page 1", "Page 2", "Page 3", "Page 4", "Page 5"].map((page) => (
        <Attachment key={page} orientation="vertical">
          <AttachmentMedia>
            <FileImageIcon aria-hidden="true" />
          </AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle>{page}</AttachmentTitle>
            <AttachmentDescription>Scan, 1.1 MB</AttachmentDescription>
          </AttachmentContent>
        </Attachment>
      ))}
    </AttachmentGroup>
  ),
};

/** A long name is cut with an ellipsis; the actions keep their place. */
export const LongName: Story = {
  render: () => (
    <FileRow
      state="done"
      title="Certificate of employment, Atelier Brume, 2019 to 2024, signed copy.pdf"
      description="PDF, 2.4 MB"
    />
  ),
};

/** On a phone, the list takes the width. */
export const OnAPhone: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-3">
      {STATES.slice(1, 4).map((file) => (
        <FileRow key={file.state} {...file} />
      ))}
    </div>
  ),
};
