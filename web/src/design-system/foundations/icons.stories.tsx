import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  BriefcaseIcon,
  CompassIcon,
  FileTextIcon,
  HeartIcon,
  type LucideIcon,
  MailIcon,
  MapPinIcon,
  MessageSquareTextIcon,
  SearchIcon,
  SendIcon,
  SettingsIcon,
  UserIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

const meta = {
  title: "Foundations/Icons",
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Lucide only, at its default 2 px stroke. Inside a component the component sets the size; an icon never stands alone without an accessible name, and never replaces a word the person needs. No emoji in the interface.",
      },
    },
  },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const ICONS: [string, LucideIcon][] = [
  ["Jobs", BriefcaseIcon],
  ["Discover", CompassIcon],
  ["Applications", SendIcon],
  ["Profile", UserIcon],
  ["CV", FileTextIcon],
  ["Like", HeartIcon],
  ["Search", SearchIcon],
  ["Location", MapPinIcon],
  ["Mail", MailIcon],
  ["Assistant", MessageSquareTextIcon],
  ["Settings", SettingsIcon],
];

const SIZES = [
  ["size-3", "12px, inside a Badge or an xs Button"],
  ["size-4", "16px, inside a Button, menu item or field"],
  ["size-5", "20px, the bottom tab bar on a phone"],
  ["size-6", "24px, an Empty state"],
] as const;

/** The sizes components use, in foreground and muted foreground. */
export const Sizes: Story = {
  render: () => (
    <div className="flex flex-col gap-4">
      {SIZES.map(([size, use]) => (
        <div key={size} className="flex flex-wrap items-center gap-4">
          <div className="flex w-64 flex-col gap-0.5">
            <code className="text-sm">{size}</code>
            <span className="text-xs text-muted-foreground">{use}</span>
          </div>
          {ICONS.slice(0, 6).map(([name, Icon]) => (
            <Icon key={name} aria-label={name} className={size} />
          ))}
          {ICONS.slice(0, 6).map(([name, Icon]) => (
            <Icon
              key={`${name}-muted`}
              aria-hidden="true"
              className={cn(size, "text-muted-foreground")}
            />
          ))}
        </div>
      ))}
    </div>
  ),
};

/** The icons that name the app's places and actions. One icon per idea, everywhere. */
export const Vocabulary: Story = {
  render: () => (
    <ul className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6">
      {ICONS.map(([name, Icon]) => (
        <li key={name} className="flex items-center gap-2 text-sm">
          <Icon aria-hidden="true" className="size-4" />
          {name}
        </li>
      ))}
    </ul>
  ),
};
