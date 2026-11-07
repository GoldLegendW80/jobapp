import type { Meta, StoryObj } from "@storybook/react-vite";

import { AuthLayout } from "@/components/shared/auth/auth-layout";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { withApp } from "@/design-system/with-app";

function SignInCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h1>Sign in</h1>
        </CardTitle>
        <CardDescription>
          With the email your invite was sent to.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
          }}
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor="story-email">Email</Label>
            <Input
              id="story-email"
              type="email"
              defaultValue="friend@example.org"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="story-password">Password</Label>
            <Input id="story-password" type="password" />
          </div>
          <Button type="submit">Sign in</Button>
        </form>
      </CardContent>
    </Card>
  );
}

const meta = {
  title: "Shared/Auth/Auth layout",
  component: AuthLayout,
  tags: ["autodocs"],
  decorators: [withApp],
  parameters: {
    layout: "fullscreen",
    docs: {
      description: {
        component:
          "The frame of the sign in pages (sign in, invite and reset links): the app's mark, a link home, above one centred card.",
      },
    },
  },
  args: { children: <SignInCard /> },
} satisfies Meta<typeof AuthLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The sign in card under the mark. */
export const SignIn: Story = { name: "Sign in" };

/** A longer card, such as the invite link's: the frame grows down, the mark stays on top. */
export const LongContent: Story = {
  name: "Long card",
  args: {
    children: (
      <Card>
        <CardHeader>
          <CardTitle>
            <h1>Create your account</h1>
          </CardTitle>
          <CardDescription>
            Harald Example invited new.friend@example.org. Choose a password of
            at least twelve characters; a sentence of four ordinary words is
            easier to remember and harder to guess than a short word with
            symbols. You can change it later on the Account page.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {["Name", "Password", "Password again"].map((label) => (
            <div key={label} className="flex flex-col gap-2">
              <Label htmlFor={`story-${label}`}>{label}</Label>
              <Input id={`story-${label}`} />
            </div>
          ))}
          <Button>Create the account</Button>
        </CardContent>
      </Card>
    ),
  },
};

/** On a phone the card takes the width, with a margin. */
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
