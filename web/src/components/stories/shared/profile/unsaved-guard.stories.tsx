import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { createMemoryRouter, Link, RouterProvider } from "react-router";
import { userEvent, within } from "storybook/test";

import { UnsavedGuard } from "@/components/shared/profile/unsaved-guard";
import { Card, CardContent } from "@/components/ui/card";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

/** A form with its own unsaved changes, and a link to another page of the app. */
function EditedForm({ what }: { what: string }) {
  const [headline, setHeadline] = useState("Senior data engineer");
  const [dirty, setDirty] = useState(false);
  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <Field className="gap-1.5">
          <FieldLabel htmlFor="guard-headline">Headline</FieldLabel>
          <Input
            id="guard-headline"
            value={headline}
            onChange={(event) => {
              setHeadline(event.target.value);
              setDirty(true);
            }}
          />
        </Field>
        <p className="text-sm text-muted-foreground">
          {dirty ? "Unsaved changes." : "Type in the field, then leave."}{" "}
          <Link to="/jobs" className="underline underline-offset-4">
            Go to Jobs
          </Link>
        </p>
        <UnsavedGuard
          dirty={dirty}
          what={what}
          onDiscard={() => {
            setDirty(false);
          }}
        />
      </CardContent>
    </Card>
  );
}

/** The guard reads the router's blocker, which a data router alone gives. */
function Harness({ what }: { what: string }) {
  const [router] = useState(() =>
    createMemoryRouter(
      [
        { path: "/profile", element: <EditedForm what={what} /> },
        {
          path: "/jobs",
          element: <p className="text-sm">The Jobs page opened.</p>,
        },
      ],
      { initialEntries: ["/profile"] },
    ),
  );
  return <RouterProvider router={router} />;
}

const meta = {
  title: "Shared/Profile/Unsaved guard",
  component: Harness,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Asks before leaving a page with unsaved changes, Keep editing or Discard changes, and lets the browser ask when the tab closes: the profile page and Settings, Search.",
      },
    },
  },
  args: { what: "Contact and links" },
} satisfies Meta<typeof Harness>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing changed yet: leaving asks nothing. */
export const NothingChanged: Story = {
  name: "Nothing changed",
};

/** An edit, then a link to another page: the guard asks first. */
export const LeavingWithChanges: Story = {
  name: "Leaving with unsaved changes",
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      canvas.getByRole("textbox", { name: "Headline" }),
      ", streaming",
    );
    await userEvent.click(canvas.getByRole("link", { name: "Go to Jobs" }));
  },
};

/** Two forms of the page hold changes: the question names both. */
export const TwoFormsChanged: Story = {
  name: "Two forms with unsaved changes",
  args: { what: "Experience and education and Contact and links" },
  play: LeavingWithChanges.play,
};
