import type { Meta, StoryObj } from "@storybook/react-vite";

import { DocumentSheet } from "@/components/shared/documents/document-sheet";
import { DocumentForm } from "@/design-system/fixtures/document-form";
import {
  SAMPLE_CV,
  SAMPLE_DAY,
  SAMPLE_FACTS,
  SAMPLE_LETTER,
} from "@/design-system/fixtures/template-sample";

const meta = {
  title: "Shared/Documents/Template pages",
  component: DocumentSheet,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Each template's CV and letter as the document page draws it, with the invented person of the templates' thumbnails: the same columns, band, photo place, accent, typeface and sizes as the PDF, each text edited where it is read. The document view shows the page of the document's template.",
      },
    },
  },
  args: {
    kind: "cv",
    language: "en",
    template: "classic",
    content: SAMPLE_CV,
    facts: SAMPLE_FACTS,
    form: null,
    known: true,
    date: SAMPLE_DAY,
  },
  render: (args) => (
    <DocumentForm
      initial={args.kind === "cv" ? SAMPLE_CV : SAMPLE_LETTER}
      kind={args.kind}
    >
      {(form) => <DocumentSheet {...args} content={form.draft} form={form} />}
    </DocumentForm>
  ),
  decorators: [
    (Story) => (
      <div className="max-w-3xl">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof DocumentSheet>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Academic: the page of its thumbnail (api/app/documents/templates/academic), edited in place. */
export const AcademicCv: Story = {
  name: "Academic CV",
  args: { template: "academic", kind: "cv" },
};

/** Academic: the page of its letter thumbnail (api/app/documents/templates/academic), edited in place. */
export const AcademicLetter: Story = {
  name: "Academic letter",
  args: { template: "academic", kind: "letter" },
};

/** Accent: the page of its thumbnail (api/app/documents/templates/accent), edited in place. */
export const AccentCv: Story = {
  name: "Accent CV",
  args: { template: "accent", kind: "cv" },
};

/** Accent: the page of its letter thumbnail (api/app/documents/templates/accent), edited in place. */
export const AccentLetter: Story = {
  name: "Accent letter",
  args: { template: "accent", kind: "letter" },
};

/** Banner: the page of its thumbnail (api/app/documents/templates/banner), edited in place. */
export const BannerCv: Story = {
  name: "Banner CV",
  args: { template: "banner", kind: "cv" },
};

/** Banner: the page of its letter thumbnail (api/app/documents/templates/banner), edited in place. */
export const BannerLetter: Story = {
  name: "Banner letter",
  args: { template: "banner", kind: "letter" },
};

/** Bold: the page of its thumbnail (api/app/documents/templates/bold), edited in place. */
export const BoldCv: Story = {
  name: "Bold CV",
  args: { template: "bold", kind: "cv" },
};

/** Bold: the page of its letter thumbnail (api/app/documents/templates/bold), edited in place. */
export const BoldLetter: Story = {
  name: "Bold letter",
  args: { template: "bold", kind: "letter" },
};

/** Cards: the page of its thumbnail (api/app/documents/templates/cards), edited in place. */
export const CardsCv: Story = {
  name: "Cards CV",
  args: { template: "cards", kind: "cv" },
};

/** Cards: the page of its letter thumbnail (api/app/documents/templates/cards), edited in place. */
export const CardsLetter: Story = {
  name: "Cards letter",
  args: { template: "cards", kind: "letter" },
};

/** Classic: the page of its thumbnail (api/app/documents/templates/classic), edited in place. */
export const ClassicCv: Story = {
  name: "Classic CV",
  args: { template: "classic", kind: "cv" },
};

/** Classic: the page of its letter thumbnail (api/app/documents/templates/classic), edited in place. */
export const ClassicLetter: Story = {
  name: "Classic letter",
  args: { template: "classic", kind: "letter" },
};

/** Columns header: the page of its thumbnail (api/app/documents/templates/columns-header), edited in place. */
export const ColumnsHeaderCv: Story = {
  name: "Columns header CV",
  args: { template: "columns-header", kind: "cv" },
};

/** Columns header: the page of its letter thumbnail (api/app/documents/templates/columns-header), edited in place. */
export const ColumnsHeaderLetter: Story = {
  name: "Columns header letter",
  args: { template: "columns-header", kind: "letter" },
};

/** Compact: the page of its thumbnail (api/app/documents/templates/compact), edited in place. */
export const CompactCv: Story = {
  name: "Compact CV",
  args: { template: "compact", kind: "cv" },
};

/** Compact: the page of its letter thumbnail (api/app/documents/templates/compact), edited in place. */
export const CompactLetter: Story = {
  name: "Compact letter",
  args: { template: "compact", kind: "letter" },
};

/** Elegant: the page of its thumbnail (api/app/documents/templates/elegant), edited in place. */
export const ElegantCv: Story = {
  name: "Elegant CV",
  args: { template: "elegant", kind: "cv" },
};

/** Elegant: the page of its letter thumbnail (api/app/documents/templates/elegant), edited in place. */
export const ElegantLetter: Story = {
  name: "Elegant letter",
  args: { template: "elegant", kind: "letter" },
};

/** Executive: the page of its thumbnail (api/app/documents/templates/executive), edited in place. */
export const ExecutiveCv: Story = {
  name: "Executive CV",
  args: { template: "executive", kind: "cv" },
};

/** Executive: the page of its letter thumbnail (api/app/documents/templates/executive), edited in place. */
export const ExecutiveLetter: Story = {
  name: "Executive letter",
  args: { template: "executive", kind: "letter" },
};

/** Grid: the page of its thumbnail (api/app/documents/templates/grid), edited in place. */
export const GridCv: Story = {
  name: "Grid CV",
  args: { template: "grid", kind: "cv" },
};

/** Grid: the page of its letter thumbnail (api/app/documents/templates/grid), edited in place. */
export const GridLetter: Story = {
  name: "Grid letter",
  args: { template: "grid", kind: "letter" },
};

/** Minimal: the page of its thumbnail (api/app/documents/templates/minimal), edited in place. */
export const MinimalCv: Story = {
  name: "Minimal CV",
  args: { template: "minimal", kind: "cv" },
};

/** Minimal: the page of its letter thumbnail (api/app/documents/templates/minimal), edited in place. */
export const MinimalLetter: Story = {
  name: "Minimal letter",
  args: { template: "minimal", kind: "letter" },
};

/** Mono: the page of its thumbnail (api/app/documents/templates/mono), edited in place. */
export const MonoCv: Story = {
  name: "Mono CV",
  args: { template: "mono", kind: "cv" },
};

/** Mono: the page of its letter thumbnail (api/app/documents/templates/mono), edited in place. */
export const MonoLetter: Story = {
  name: "Mono letter",
  args: { template: "mono", kind: "letter" },
};

/** Nordic: the page of its thumbnail (api/app/documents/templates/nordic), edited in place. */
export const NordicCv: Story = {
  name: "Nordic CV",
  args: { template: "nordic", kind: "cv" },
};

/** Nordic: the page of its letter thumbnail (api/app/documents/templates/nordic), edited in place. */
export const NordicLetter: Story = {
  name: "Nordic letter",
  args: { template: "nordic", kind: "letter" },
};

/** Plain: the page of its thumbnail (api/app/documents/templates/plain), edited in place. */
export const PlainCv: Story = {
  name: "Plain CV",
  args: { template: "plain", kind: "cv" },
};

/** Plain: the page of its letter thumbnail (api/app/documents/templates/plain), edited in place. */
export const PlainLetter: Story = {
  name: "Plain letter",
  args: { template: "plain", kind: "letter" },
};

/** Portrait: the page of its thumbnail (api/app/documents/templates/portrait), edited in place. */
export const PortraitCv: Story = {
  name: "Portrait CV",
  args: { template: "portrait", kind: "cv" },
};

/** Portrait: the page of its letter thumbnail (api/app/documents/templates/portrait), edited in place. */
export const PortraitLetter: Story = {
  name: "Portrait letter",
  args: { template: "portrait", kind: "letter" },
};

/** Serif: the page of its thumbnail (api/app/documents/templates/serif), edited in place. */
export const SerifCv: Story = {
  name: "Serif CV",
  args: { template: "serif", kind: "cv" },
};

/** Serif: the page of its letter thumbnail (api/app/documents/templates/serif), edited in place. */
export const SerifLetter: Story = {
  name: "Serif letter",
  args: { template: "serif", kind: "letter" },
};

/** Sidebar: the page of its thumbnail (api/app/documents/templates/sidebar), edited in place. */
export const SidebarCv: Story = {
  name: "Sidebar CV",
  args: { template: "sidebar", kind: "cv" },
};

/** Sidebar: the page of its letter thumbnail (api/app/documents/templates/sidebar), edited in place. */
export const SidebarLetter: Story = {
  name: "Sidebar letter",
  args: { template: "sidebar", kind: "letter" },
};

/** Split right: the page of its thumbnail (api/app/documents/templates/split-right), edited in place. */
export const SplitRightCv: Story = {
  name: "Split right CV",
  args: { template: "split-right", kind: "cv" },
};

/** Split right: the page of its letter thumbnail (api/app/documents/templates/split-right), edited in place. */
export const SplitRightLetter: Story = {
  name: "Split right letter",
  args: { template: "split-right", kind: "letter" },
};

/** Timeline: the page of its thumbnail (api/app/documents/templates/timeline), edited in place. */
export const TimelineCv: Story = {
  name: "Timeline CV",
  args: { template: "timeline", kind: "cv" },
};

/** Timeline: the page of its letter thumbnail (api/app/documents/templates/timeline), edited in place. */
export const TimelineLetter: Story = {
  name: "Timeline letter",
  args: { template: "timeline", kind: "letter" },
};

/** A French letter: the date and the subject's word print in French. */
export const FrenchLetter: Story = {
  name: "A letter in French",
  args: { template: "elegant", kind: "letter", language: "fr" },
};

/** On a phone the page keeps its template, its text no smaller than the app's small type. */
export const OnAPhone: Story = {
  name: "Sidebar CV on a phone",
  args: { template: "sidebar", kind: "cv" },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};
