import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { userEvent, within } from "storybook/test";

import { HistoryDrawer } from "@/components/shared/profile/history-drawer";
import { camilleProfile } from "@/design-system/fixtures/camille-profile";
import { withApp } from "@/design-system/with-app";
import type { SectionName } from "@/features/profile/api/profile-api";
import { useProfile } from "@/features/profile/api/use-profile";
import { fakeProfile, revisionTime } from "@/test/profile";
import { server } from "@/test/server";

/** The drawer over the profile the page loaded, open unless it has its own button. */
function Harness({
  section,
  open = true,
  dirty = false,
}: {
  section: SectionName;
  open?: boolean;
  dirty?: boolean;
}) {
  const profile = useProfile();
  if (!profile.data) return null;
  return (
    <HistoryDrawer
      section={section}
      profile={profile.data}
      dirty={dirty}
      onRestored={() => undefined}
      {...(open ? { open, onOpenChange: () => undefined } : {})}
    />
  );
}

/** Camille's profile at revision 12, with two older saves of Experience and one of Facts. */
function camilleWithPastSaves() {
  const now = camilleProfile();
  const profile = fakeProfile(now, 12);
  const before = structuredClone(now.experience);
  before.employers = before.employers.slice(1);
  const firstTitle = structuredClone(now.experience);
  firstTitle.employers = firstTitle.employers.map((one, index) =>
    index === 0 ? { ...one, title: "Data engineer" } : one,
  );
  const facts = structuredClone(now.facts);
  facts.blocks = facts.blocks.map((block, index) =>
    index === 0
      ? { ...block, body: `${block.body}\n\nI also ran the on call rota.` }
      : block,
  );
  profile.revisions.push(
    { revision: 2, section: "facts", created_at: revisionTime(2), data: facts },
    {
      revision: 3,
      section: "experience",
      created_at: revisionTime(3),
      data: before,
    },
    {
      revision: 5,
      section: "experience",
      created_at: revisionTime(5),
      data: firstTitle,
    },
  );
  return profile;
}

/** Chooses a version of the open drawer. */
const choose =
  (revision: number) =>
  async ({ canvasElement }: { canvasElement: HTMLElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(
      await body.findByRole("button", {
        name: new RegExp(`^Revision ${String(revision)},`),
      }),
    );
  };

const meta = {
  title: "Shared/Profile/History drawer",
  component: Harness,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "Every save of one section of the profile, newest first: a version compared with the current one, field by field or line by line, and restored as a new save. Opened from History under More actions on the profile page, and from History on Settings, Search.",
      },
    },
  },
  args: { section: "experience" },
  beforeEach: () => {
    camilleWithPastSaves();
  },
} satisfies Meta<typeof Harness>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The saves of Experience, none chosen yet. */
export const PastSaves: Story = {
  name: "Past saves of a section",
};

/** One older save beside the current version: the fields that differ are marked. */
export const OneCompared: Story = {
  name: "A version compared with the current one",
  play: choose(5),
};

/** The facts compare line by line: what only one of the two versions has. */
export const FactsCompared: Story = {
  name: "Facts compared line by line",
  args: { section: "facts" },
  play: choose(2),
};

/** The section has unsaved changes: restoring asks before it throws them away. */
export const RestoreWithUnsavedChanges: Story = {
  name: "Restoring over unsaved changes asks first",
  args: { dirty: true },
  play: async (context) => {
    await choose(5)(context);
    const body = within(context.canvasElement.ownerDocument.body);
    await userEvent.click(
      await body.findByRole("button", { name: "Restore this version" }),
    );
  },
};

/** A section never saved has no version to show. */
export const NeverSaved: Story = {
  name: "A section never saved",
  beforeEach: () => {
    fakeProfile(null);
  },
};

/** The list could not be loaded: the reason, and a way to try again. */
export const ListFailed: Story = {
  name: "The versions could not be loaded",
  beforeEach: () => {
    server.use(
      http.get("*/api/profile/revisions", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** One version could not be loaded: said in place of the comparison. */
export const VersionFailed: Story = {
  name: "A version that could not be loaded",
  beforeEach: () => {
    server.use(
      http.get("*/api/profile/revisions/:revision", () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
  play: choose(5),
};

/** Closed, the drawer is its History button, which Settings, Search shows in its header. */
export const HistoryButton: Story = {
  name: "The History button",
  args: { section: "search", open: false },
};

/** On a phone the list and the comparison take turns, full width. */
export const OnAPhone: Story = {
  name: "On a phone",
  globals: { viewport: { value: "mobile2", isRotated: false } },
  play: choose(5),
};
