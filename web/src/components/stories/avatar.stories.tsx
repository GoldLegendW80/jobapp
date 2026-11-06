import type { Meta, StoryObj } from "@storybook/react-vite";

import {
  Avatar,
  AvatarBadge,
  AvatarFallback,
  AvatarGroup,
  AvatarGroupCount,
  AvatarImage,
} from "@/components/ui/avatar";

// Invented portraits drawn inline, so the stories need no photo of a real person.
function portrait(
  background: string,
  skin: string,
  hair: string,
  shirt: string,
) {
  return `data:image/svg+xml;utf8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect width="80" height="80" fill="${background}"/><path d="M14 80c2-17 13-25 26-25s24 8 26 25z" fill="${shirt}"/><circle cx="40" cy="34" r="15" fill="${skin}"/><path d="M24 32c0-12 8-18 16-18s16 6 16 18c-4-6-10-8-16-8s-12 2-16 8z" fill="${hair}"/></svg>`,
  )}`;
}

const PHOTO = portrait("#cdd6ea", "#e8b48f", "#3a2a22", "#2f3f73");

const FRIENDS = [
  { name: "Camille Martin", initials: "CM", photo: PHOTO },
  {
    name: "Hugo Lefebvre",
    initials: "HL",
    photo: portrait("#e6dccb", "#c98e6a", "#1f1a17", "#7a4b2a"),
  },
  {
    name: "Inès Moreau",
    initials: "IM",
    photo: portrait("#d4e6dc", "#8d5a3c", "#14110f", "#2e6b4f"),
  },
  {
    name: "Théo Garnier",
    initials: "TG",
    photo: portrait("#ead3d3", "#f0c7a6", "#b3742f", "#5a3a6e"),
  },
];

const meta = {
  title: "Components/Data display/Avatar",
  component: Avatar,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A person: the account in the header menu, a friend invited to the app, the photo on a profile. Always with initials in AvatarFallback, which show while the photo loads and when there is none.",
      },
    },
  },
} satisfies Meta<typeof Avatar>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The person's photo, with their initials behind it. */
export const Typical: Story = {
  render: () => (
    <Avatar>
      <AvatarImage src={PHOTO} alt="Camille Martin" />
      <AvatarFallback>CM</AvatarFallback>
    </Avatar>
  ),
};

/** No photo: the initials are the avatar. Most accounts look like this. */
export const Initials: Story = {
  render: () => (
    <Avatar>
      <AvatarFallback>CM</AvatarFallback>
    </Avatar>
  ),
};

/** sm in table rows and lists, default in the header, lg on the profile. */
export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      {(["sm", "default", "lg"] as const).map((size) => (
        <div key={size} className="flex items-center gap-2">
          <Avatar size={size}>
            <AvatarImage src={PHOTO} alt="Camille Martin" />
            <AvatarFallback>CM</AvatarFallback>
          </Avatar>
          <Avatar size={size}>
            <AvatarFallback>CM</AvatarFallback>
          </Avatar>
        </div>
      ))}
    </div>
  ),
};

/** Beside a name and e-mail, as at the top of the account menu. The avatar is then decorative. */
export const WithName: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Avatar aria-hidden="true">
        <AvatarImage src={PHOTO} alt="" />
        <AvatarFallback>CM</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-sm font-medium">Camille Martin</span>
        <span className="truncate text-xs text-muted-foreground">
          camille.martin@example.org
        </span>
      </div>
    </div>
  ),
};

/** A dot for a state worth noticing, such as an invitation not accepted yet. Say it in words too. */
export const WithBadge: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Avatar size="lg">
        <AvatarFallback>IM</AvatarFallback>
        <AvatarBadge>
          <span className="sr-only">Invitation pending</span>
        </AvatarBadge>
      </Avatar>
      <div className="flex flex-col">
        <span className="text-sm font-medium">Inès Moreau</span>
        <span className="text-xs text-muted-foreground">
          Invited on 2 November, not signed in yet
        </span>
      </div>
    </div>
  ),
};

/** Several people at once, such as the friends who use this app. The count stands for the rest; each avatar hides the edge of the one before it. */
export const Group: Story = {
  render: () => (
    <AvatarGroup>
      {FRIENDS.map((friend) => (
        <Avatar key={friend.name}>
          <AvatarImage src={friend.photo} alt={friend.name} />
          <AvatarFallback>{friend.initials}</AvatarFallback>
        </Avatar>
      ))}
      <AvatarGroupCount>
        +3<span className="sr-only"> more people</span>
      </AvatarGroupCount>
    </AvatarGroup>
  ),
};

/** The photo failed to load: the initials take its place, nothing looks broken. */
export const MissingImage: Story = {
  render: () => (
    <Avatar size="lg">
      <AvatarImage src="/photos/camille-martin.jpg" alt="Camille Martin" />
      <AvatarFallback>CM</AvatarFallback>
    </Avatar>
  ),
};

/** A long name: the avatar keeps its size, the name truncates beside it. */
export const LongName: Story = {
  render: () => (
    <div className="flex w-64 items-center gap-3">
      <Avatar aria-hidden="true">
        <AvatarFallback>MD</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-sm font-medium">
          Marie-Christine de La Tour-Dupontel
        </span>
        <span className="truncate text-xs text-muted-foreground">
          marie-christine.delatour-dupontel@example.org
        </span>
      </div>
    </div>
  ),
};
