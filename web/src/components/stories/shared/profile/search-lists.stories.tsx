import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { useForm } from "react-hook-form";

import {
  SearchLists,
  type SearchListName,
} from "@/components/shared/profile/search-lists";
import { Card, CardContent } from "@/components/ui/card";
import { Form } from "@/components/ui/form";
import {
  camilleProfile,
  TITLE_IDEAS,
} from "@/design-system/fixtures/camille-profile";
import { withApp } from "@/design-system/with-app";
import { EMPTY, type Search } from "@/features/profile/api/profile-api";
import { useProfile } from "@/features/profile/api/use-profile";
import {
  toSearchForm,
  type SearchForm,
} from "@/features/profile/helpers/editor-forms";
import { fakeProfile } from "@/test/profile";
import { server } from "@/test/server";

const camilleSearch = (): Search => camilleProfile().search;

/** The lists in a search form of their own, once the profile (the place idea) is read. */
function Harness({
  search = camilleSearch(),
  descriptions = true,
  links = true,
  only,
}: {
  search?: Search;
  descriptions?: boolean;
  links?: boolean;
  only?: readonly SearchListName[];
}) {
  const profile = useProfile();
  const form = useForm<SearchForm>({ defaultValues: toSearchForm(search) });
  if (!profile.data) return null;
  return (
    <Form {...form}>
      <Card>
        <CardContent>
          <SearchLists
            form={form}
            descriptions={descriptions}
            links={links}
            only={only}
          />
        </CardContent>
      </Card>
    </Form>
  );
}

const meta = {
  title: "Shared/Profile/Search lists",
  component: Harness,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    docs: {
      description: {
        component:
          "The four word lists of the search, Job titles, Unwanted titles, Places and Unwanted places, as chips, with the job title ideas and the place of the profile to add in one click: Settings, Search and Find jobs.",
      },
    },
  },
  beforeEach: () => {
    fakeProfile(camilleProfile());
  },
} satisfies Meta<typeof Harness>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Camille's lists in words; no model key, so the ideas say what they need. */
export const Lists: Story = {
  name: "Titles and places in words",
};

/** With a model key, the job titles agent's ideas under Job titles. */
export const WithIdeas: Story = {
  name: "Job title ideas under Job titles",
  beforeEach: () => {
    server.use(
      http.get("*/api/profile/title-suggestions", () =>
        HttpResponse.json(TITLE_IDEAS),
      ),
    );
  },
};

/** A new search: every list empty, and the place of the profile offered under Places. */
export const Empty: Story = {
  name: "Nothing listed yet",
  args: { search: structuredClone(EMPTY.search) },
};

/** A list an earlier version saved as regular expressions keeps working and says so. */
export const SavedAsPatterns: Story = {
  name: "A list saved as patterns",
  args: {
    search: {
      ...camilleSearch(),
      location_exclude: { advanced_regex: true, terms: ["\\bUSA?\\b"] },
    },
  },
};

/** In Find jobs, a dialog: no one line explanations, and no link that would drop the draft. */
export const InADialog: Story = {
  name: "In a dialog",
  args: {
    descriptions: false,
    links: false,
    only: ["title_include", "location"],
  },
};

/** The two lists of what to leave out, as Find jobs folds them under More options. */
export const OnlyWhatToLeaveOut: Story = {
  name: "Only what to leave out",
  args: { only: ["title_exclude", "location_exclude"] },
};

/** On a phone the chips wrap under each list's name. */
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
