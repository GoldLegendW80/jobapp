import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  ActivityIcon,
  ColumnsIcon,
  ExternalLinkIcon,
  FileTextIcon,
  LogOutIcon,
  MailIcon,
  MoreHorizontalIcon,
  SettingsIcon,
  Trash2Icon,
  UserRoundIcon,
  XIcon,
} from "lucide-react";
import { useState } from "react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

// Shown in its own iframe on the docs page, so the open menu does not cover the page.
const OPEN_FOR_REVIEW = {
  docs: { story: { inline: false, height: "420px" } },
} as const;

function PostingMenu({ defaultOpen }: { defaultOpen?: boolean }) {
  return (
    <DropdownMenu defaultOpen={defaultOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="More actions">
          <MoreHorizontalIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuGroup>
          <DropdownMenuItem>
            <ExternalLinkIcon aria-hidden="true" />
            Open the posting page
          </DropdownMenuItem>
          <DropdownMenuItem>
            <FileTextIcon aria-hidden="true" />
            Write a CV
          </DropdownMenuItem>
          <DropdownMenuItem>
            <MailIcon aria-hidden="true" />
            Write a cover letter
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem>
            <XIcon aria-hidden="true" />
            Pass
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive">
            <Trash2Icon aria-hidden="true" />
            Delete posting
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const COLUMNS = ["Status", "Fit", "Pay", "Work mode", "Updated"] as const;

function ColumnsMenu() {
  const [shown, setShown] = useState<string[]>(["Status", "Fit", "Updated"]);
  return (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          <ColumnsIcon />
          Columns
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-48">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Show in the list</DropdownMenuLabel>
          {COLUMNS.map((column) => (
            <DropdownMenuCheckboxItem
              key={column}
              checked={shown.includes(column)}
              onCheckedChange={(checked) => {
                setShown((current) =>
                  checked
                    ? [...current, column]
                    : current.filter((one) => one !== column),
                );
              }}
              onSelect={(event) => {
                event.preventDefault();
              }}
            >
              {column}
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SortMenu() {
  const [order, setOrder] = useState("fit");
  return (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          Sort
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-48">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Sort postings by</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={order} onValueChange={setOrder}>
            <DropdownMenuRadioItem value="fit">Fit score</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="newest">Newest</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="pay">
              Highest pay
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const meta = {
  title: "Components/Overlays/Dropdown menu",
  component: DropdownMenu,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "The less frequent actions of one thing, behind a single button: the More actions of a posting or a document, the account menu. Items sit in groups; the destructive one comes last, apart, in red. The frequent action stays a visible button.",
      },
    },
  },
} satisfies Meta<typeof DropdownMenu>;

export default meta;
type Story = StoryObj<typeof meta>;

/** More actions of a posting: an icon button named for screen readers. Click to open. */
export const Typical: Story = {
  render: () => <PostingMenu />,
};

/** Open, for review: two groups, the destructive item last. */
export const Open: Story = {
  render: () => <PostingMenu defaultOpen />,
  parameters: OPEN_FOR_REVIEW,
};

/** The account menu: who is signed in as a label, then the pages, then Sign out. */
export const AccountMenu: Story = {
  render: () => (
    <div className="flex justify-end">
      <DropdownMenu defaultOpen>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Account menu">
            <Avatar>
              <AvatarFallback>CM</AvatarFallback>
            </Avatar>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="w-64 max-w-[calc(100vw-2rem)]"
        >
          <DropdownMenuGroup>
            <DropdownMenuLabel className="flex flex-col gap-0.5 font-normal">
              <span className="truncate font-medium">Camille Martin</span>
              <span className="truncate text-xs text-muted-foreground">
                camille.martin@example.org
              </span>
            </DropdownMenuLabel>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem>
              <UserRoundIcon aria-hidden="true" />
              Account
            </DropdownMenuItem>
            <DropdownMenuItem>
              <SettingsIcon aria-hidden="true" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem>
              <ActivityIcon aria-hidden="true" />
              Assistant activity
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem>
              <LogOutIcon aria-hidden="true" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** Checkboxes for choices that add up, such as the columns of a list. The menu stays open. */
export const WithCheckboxes: Story = {
  render: () => <ColumnsMenu />,
  parameters: OPEN_FOR_REVIEW,
};

/** Radio items for one choice out of a few, such as the order of a list. */
export const WithRadio: Story = {
  render: () => <SortMenu />,
  parameters: OPEN_FOR_REVIEW,
};

/** A submenu for a choice inside an action, such as the status to move a posting to. */
export const WithSubmenu: Story = {
  render: () => (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="More actions">
          <MoreHorizontalIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-52">
        <DropdownMenuGroup>
          <DropdownMenuItem>
            <ExternalLinkIcon aria-hidden="true" />
            Open the posting page
          </DropdownMenuItem>
          <DropdownMenuSub defaultOpen>
            <DropdownMenuSubTrigger>Move to</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuGroup>
                <DropdownMenuItem>Shortlisted</DropdownMenuItem>
                <DropdownMenuItem>Applied</DropdownMenuItem>
                <DropdownMenuItem>Interview</DropdownMenuItem>
                <DropdownMenuItem>Rejected</DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
  parameters: OPEN_FOR_REVIEW,
};

/** Long labels wrap inside a menu of fixed width; an item that cannot run now is disabled. */
export const LongText: Story = {
  render: () => (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="More actions">
          <MoreHorizontalIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuGroup>
          <DropdownMenuItem>
            <FileTextIcon aria-hidden="true" />
            Write a CV from the base CV and this posting, in French
          </DropdownMenuItem>
          <DropdownMenuItem disabled>
            <MailIcon aria-hidden="true" />
            Write a cover letter (after the CV)
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
  parameters: OPEN_FOR_REVIEW,
};
