import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { useLocation } from "react-router";

import { OnboardingGate } from "@/components/shared/onboarding/onboarding-gate";
import { firstSignIn } from "@/design-system/fixtures/onboarding";
import { withApp } from "@/design-system/with-app";
import { useMe } from "@/features/auth/api/use-me";
import { fakeSession, member } from "@/test/auth";
import { server } from "@/test/server";

/** The gate sits under the sign in guard: it mounts once the person is known. */
function SignedIn({ children }: { children: ReactNode }) {
  const me = useMe();
  return me.data ? children : null;
}

/** The page the person asked for, saying where it is. */
function AskedPage() {
  const { pathname, search } = useLocation();
  return (
    <div className="rounded-xl border bg-card p-6 text-sm">
      The page at <code>{`${pathname}${search}`}</code>
    </div>
  );
}

function answerOnboarding(respond: () => Promise<Response> | Response) {
  server.use(http.get("*/api/opportunities/onboarding", respond));
}

const meta = {
  title: "Shared/Onboarding/Onboarding gate",
  component: OnboardingGate,
  tags: ["autodocs"],
  decorators: [
    (Story) => (
      <SignedIn>
        <Story />
      </SignedIn>
    ),
    withApp,
  ],
  parameters: {
    at: "/jobs",
    docs: {
      description: {
        component:
          "Around every signed in page: once per browser session it asks whether the first login wizard should open, shows grey bars meanwhile, and sends a new member with an empty profile to /welcome, keeping a deep link for after the wizard.",
      },
    },
  },
  args: { children: <AskedPage /> },
  beforeEach: () => {
    fakeSession(member);
    // Once per browser session: forget the answer of an earlier story.
    try {
      window.sessionStorage.removeItem(`welcome:checked:${member.id}`);
    } catch {
      // Storage blocked: the gate asks the api, as on a first visit.
    }
  },
} satisfies Meta<typeof OnboardingGate>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The wizard is done: the page asked for shows. */
export const PageShows: Story = { name: "Wizard done, the page shows" };

/** The answer has not come yet: grey bars hold the page's place. */
export const Waiting: Story = {
  name: "Waiting for the answer",
  beforeEach: () => {
    answerOnboarding(async () => {
      await delay("infinite");
      return HttpResponse.json(firstSignIn);
    });
  },
};

/** A new member with an empty profile goes to the wizard. */
export const SentToTheWizard: Story = {
  name: "A new member goes to the wizard",
  beforeEach: () => {
    answerOnboarding(() => HttpResponse.json(firstSignIn));
  },
};

/** A deep link is kept in the address, for the wizard to open at its end. */
export const DeepLinkKept: Story = {
  name: "A deep link is kept for after the wizard",
  parameters: { at: "/applications?tab=queue" },
  beforeEach: () => {
    answerOnboarding(() => HttpResponse.json(firstSignIn));
  },
};

/** The api fails: nobody is sent anywhere, the page shows. */
export const ApiFails: Story = {
  name: "The answer fails, the page shows",
  beforeEach: () => {
    answerOnboarding(() =>
      HttpResponse.json(
        { detail: "Something went wrong on the server." },
        { status: 500 },
      ),
    );
  },
};
