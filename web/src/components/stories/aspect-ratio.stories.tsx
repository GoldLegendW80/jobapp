import type { Meta, StoryObj } from "@storybook/react-vite";
import { FileTextIcon, MonitorIcon } from "lucide-react";

import { AspectRatio } from "@/components/ui/aspect-ratio";
import { Skeleton } from "@/components/ui/skeleton";

/** A page drawn in CSS, as the template thumbnails show a CV. */
function Page({ name }: { name: string }) {
  return (
    <div className="flex size-full flex-col gap-2 rounded-sm border bg-white p-3 text-[8px] text-neutral-800">
      <span className="text-[11px] font-semibold">Camille Martin</span>
      <span className="text-neutral-500">Senior data engineer, Lyon</span>
      <span className="mt-2 h-1 w-3/4 rounded-full bg-neutral-200" />
      <span className="h-1 w-2/3 rounded-full bg-neutral-200" />
      <span className="h-1 w-4/5 rounded-full bg-neutral-200" />
      <span className="mt-auto text-neutral-400">{name}</span>
    </div>
  );
}

const meta = {
  title: "Components/Data display/Aspect ratio",
  component: AspectRatio,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Holds a box at a ratio while its width changes, so a grid of thumbnails lines up before the images load: an A4 page (210 by 297) for templates, the browser's screen (16 by 10) for the live view.",
      },
    },
  },
  args: { ratio: 210 / 297 },
} satisfies Meta<typeof AspectRatio>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A template's thumbnail: an A4 page. */
export const Typical: Story = {
  render: (args) => (
    <div className="w-40">
      <AspectRatio {...args}>
        <Page name="Sidebar" />
      </AspectRatio>
    </div>
  ),
};

/** The ratios the app uses: a page, the browser's screen. */
export const Ratios: Story = {
  render: () => (
    <div className="flex flex-wrap items-start gap-6">
      <figure className="flex w-40 flex-col gap-2">
        <AspectRatio ratio={210 / 297}>
          <Page name="Classic" />
        </AspectRatio>
        <figcaption className="text-xs text-muted-foreground">
          A4 page, 210 by 297
        </figcaption>
      </figure>
      <figure className="flex w-80 flex-col gap-2">
        <AspectRatio
          ratio={16 / 10}
          className="flex items-center justify-center rounded-md border bg-muted text-muted-foreground"
        >
          <MonitorIcon aria-hidden="true" className="size-6" />
        </AspectRatio>
        <figcaption className="text-xs text-muted-foreground">
          The browser&apos;s screen, 16 by 10
        </figcaption>
      </figure>
    </div>
  ),
};

/** Loading: the skeleton has the thumbnail's shape, so nothing moves when it arrives. */
export const Loading: Story = {
  render: () => (
    <div className="grid w-full max-w-2xl grid-cols-2 gap-4 sm:grid-cols-4">
      {Array.from({ length: 4 }, (_, index) => (
        <AspectRatio key={index} ratio={210 / 297}>
          <Skeleton className="size-full" />
        </AspectRatio>
      ))}
    </div>
  ),
};

/** No picture: an icon and a word in the same frame. */
export const NoImage: Story = {
  render: () => (
    <div className="w-40">
      <AspectRatio
        ratio={210 / 297}
        className="flex flex-col items-center justify-center gap-2 rounded-sm border border-dashed bg-muted text-xs text-muted-foreground"
      >
        <FileTextIcon aria-hidden="true" className="size-5" />
        No preview yet
      </AspectRatio>
    </div>
  ),
};

/** A grid of templates: more columns on a wide screen, two on a phone. */
export const Grid: Story = {
  render: () => (
    <div className="grid w-full grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {["Classic", "Sidebar", "Compact", "Modern", "Timeline"].map((name) => (
        <AspectRatio key={name} ratio={210 / 297}>
          <Page name={name} />
        </AspectRatio>
      ))}
    </div>
  ),
};
