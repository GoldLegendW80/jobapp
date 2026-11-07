import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { useState } from "react";

import {
  PlaceSuggestion,
  TitleSuggestions,
} from "@/components/shared/profile/title-suggestions";
import { Card, CardContent } from "@/components/ui/card";
import {
  camilleProfile,
  TITLE_IDEAS,
} from "@/design-system/fixtures/camille-profile";
import { withApp } from "@/design-system/with-app";
import { useProfile } from "@/features/profile/api/use-profile";
import type { TitleSuggestions as IdeasAnswer } from "@/features/profile/api/title-suggestions-api";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

const URL = "*/api/profile/title-suggestions";

/** GET answers `ideas`; More ideas and Send wait, so the story shows the run on its way. */
function fakeIdeas(patch: Partial<IdeasAnswer> = {}) {
  const ideas = { ...TITLE_IDEAS, ...patch };
  server.use(
    http.get(URL, () => HttpResponse.json(ideas)),
    http.post(URL, async () => {
      await delay("infinite");
      return HttpResponse.json(ideas);
    }),
  );
}

/** The ideas under a list of job titles, which a click on an idea adds to. */
function Harness({
  links = true,
  quiet = false,
}: {
  links?: boolean;
  quiet?: boolean;
}) {
  const [titles, setTitles] = useState(["data engineer"]);
  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm">
          <span className="font-medium">Job titles:</span> {titles.join(", ")}
        </p>
        <TitleSuggestions
          current={titles}
          onAdd={(title) => {
            setTitles((list) => [...list, title]);
          }}
          label="Job titles"
          links={links}
          quiet={quiet}
        />
      </CardContent>
    </Card>
  );
}

const meta = {
  title: "Shared/Profile/Title suggestions",
  component: Harness,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The job titles agent's ideas under Job titles, each added in one click or dismissed, with More ideas and a line to tell the assistant what you want: Settings, Search, Find jobs and the first login wizard.",
      },
    },
  },
  beforeEach: () => {
    fakeIdeas();
  },
} satisfies Meta<typeof Harness>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Three ideas with their reasons; a click adds one to the list above. */
export const Ideas: Story = {
  name: "Ideas to add in one click",
};

/** A run on its way: the ideas so far stay, and a line says the assistant is looking. */
export const Looking: Story = {
  name: "Looking for more ideas",
  beforeEach: () => {
    fakeIdeas({ status: "running" });
  },
};

/** The first run, nothing shown yet: grey chips in their place. */
export const FirstRun: Story = {
  name: "The first ideas on their way",
  beforeEach: () => {
    fakeIdeas({ status: "running", suggestions: [], refreshed_at: null });
  },
};

/** A run failed: its reason, and Try again. */
export const Failed: Story = {
  name: "A run that failed",
  beforeEach: () => {
    fakeIdeas({
      status: "failed",
      suggestions: [],
      error: "The assistant could not finish. Your model key was refused.",
    });
  },
};

/** Never asked yet: the button that asks for the first ideas. */
export const NeverAsked: Story = {
  name: "Never asked yet",
  beforeEach: () => {
    fakeIdeas({ suggestions: [], refreshed_at: null });
  },
};

/** Every idea is already in the list or dismissed. */
export const NothingNew: Story = {
  name: "No new title to suggest",
  beforeEach: () => {
    fakeIdeas({ suggestions: [] });
  },
};

/** What the person told the assistant shows under the box, with Clear. */
export const AfterAMessage: Story = {
  name: "After a message to the assistant",
  beforeEach: () => {
    fakeIdeas({
      messages: [
        {
          text: "Stay in data, closer to the product",
          at: "2026-11-04T08:00:00Z",
        },
        { text: "Remote or Lyon only", at: "2026-11-05T08:00:00Z" },
      ],
    });
  },
};

/** No model key: the line that sends to the Account page. */
export const NoModelKey: Story = {
  name: "No model key yet",
  beforeEach: () => {
    fakeIdeas({ needs: "model_key", suggestions: [], estimate: null });
  },
};

/** In a dialog the same line names the page without a link, which would drop the draft. */
export const NoModelKeyInADialog: Story = {
  name: "No model key yet, in a dialog",
  args: { links: false },
  beforeEach: NoModelKey.beforeEach,
};

/** Nothing to read yet: a CV, the profile or a message to the assistant. */
export const NothingToRead: Story = {
  name: "Nothing to read yet",
  beforeEach: () => {
    fakeIdeas({ needs: "input", suggestions: [] });
  },
};

/** The ideas could not be loaded: the reason and Try again. */
export const LoadFailed: Story = {
  name: "The ideas could not be loaded",
  beforeEach: () => {
    server.use(
      http.get(URL, () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );
  },
};

/** On a phone the ideas wrap, each with its reason under it. */
export const OnAPhone: Story = {
  name: "On a phone",
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};

/** PlaceSuggestion reads the profile the page loaded: here the harness loads it. */
function PlaceHarness() {
  const profile = useProfile();
  const [places, setPlaces] = useState<string[]>([]);
  if (!profile.data) return null;
  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm">
          <span className="font-medium">Places:</span>{" "}
          {places.length > 0 ? places.join(", ") : "none yet"}
        </p>
        <PlaceSuggestion
          current={places}
          onAdd={(place) => {
            setPlaces((list) => [...list, place]);
          }}
          label="Places"
        />
      </CardContent>
    </Card>
  );
}

/** Under Places, the city of the profile, added in one click while the list lacks it. */
export const PlaceOfTheProfile: Story = {
  name: "The place of the profile under Places",
  render: () => <PlaceHarness />,
  beforeEach: () => {
    fakeProfile(camilleProfile());
  },
};
