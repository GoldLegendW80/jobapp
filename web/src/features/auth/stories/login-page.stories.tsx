import type { Meta, StoryObj } from "@storybook/react-vite";
import { delay, http, HttpResponse } from "msw";
import { expect, userEvent, within } from "storybook/test";

import { AppAt } from "@/design-system/app-at";
import { fakeLogin } from "@/design-system/fixtures/sign-in";
import { fakeSession, member, unauthorized } from "@/test/auth";
import { server } from "@/test/server";

const meta = {
  title: "Pages/Sign in/Sign in",
  parameters: {
    layout: "fullscreen",
    page: true,
    docs: {
      description: {
        component:
          "The e-mail address and password of an account, one card under the app's mark. Route /login; a protected page sends a signed out visitor here with the page in `next`.",
      },
    },
  },
  render: () => <AppAt path="/login" />,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

/** Types the invented credentials of a story and presses Sign in. */
async function signIn(canvasElement: HTMLElement) {
  const page = within(canvasElement);
  await userEvent.type(
    await page.findByLabelText("Email"),
    "friend@example.org",
  );
  await userEvent.type(page.getByLabelText("Password"), "wrong horse");
  await userEvent.click(page.getByRole("button", { name: "Sign in" }));
  return page;
}

/** As an invited friend, I want to sign in with my e-mail address and password, so that I reach my jobs. */
export const SignInWithEmail: Story = {
  beforeEach: () => {
    fakeLogin(() => HttpResponse.json(member));
  },
};

/** As an invited friend, I want each empty field marked with what to type, so that I know why nothing happened. This story presses Sign in itself (play). */
export const FieldsLeftEmpty: Story = {
  beforeEach: () => {
    fakeLogin(() => HttpResponse.json(member));
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    await userEvent.click(await page.findByRole("button", { name: "Sign in" }));
    await expect(
      await page.findByText("Enter your email address."),
    ).toBeVisible();
    await expect(page.getByText("Enter your password.")).toBeVisible();
  },
};

/** As an invited friend who mistyped, I want to be told the e-mail or password is wrong without saying which, so that I try again and nobody learns who has an account. This story signs in itself (play). */
export const WrongPassword: Story = {
  beforeEach: () => {
    fakeLogin(() =>
      HttpResponse.json(
        { detail: "Incorrect email or password." },
        { status: 401 },
      ),
    );
  },
  play: async ({ canvasElement }) => {
    const page = await signIn(canvasElement);
    await expect(
      await page.findByText("Incorrect email or password."),
    ).toBeVisible();
  },
};

/** As an invited friend who tried too often, I want the button to count down until I may try again, so that I do not keep trying in vain. This story signs in itself (play). */
export const TooManyAttempts: Story = {
  beforeEach: () => {
    fakeLogin(() =>
      HttpResponse.json(
        {
          detail:
            "Too many sign in attempts. Wait a few minutes, then try again.",
        },
        { status: 429, headers: { "Retry-After": "65" } },
      ),
    );
  },
  play: async ({ canvasElement }) => {
    const page = await signIn(canvasElement);
    await expect(
      await page.findByRole("button", { name: /Try again in/ }),
    ).toBeDisabled();
  },
};

/** As an invited friend on a bad connection, I want to be told the server could not be reached, so that I check my connection instead of my password. This story signs in itself (play). */
export const ServerUnreachable: Story = {
  beforeEach: () => {
    fakeLogin(() => HttpResponse.error());
  },
  play: async ({ canvasElement }) => {
    const page = await signIn(canvasElement);
    await expect(
      await page.findByText(/The server could not be reached/),
    ).toBeVisible();
  },
};

/** As an invited friend, I want the button to say it is signing me in and to take one click only, so that I wait instead of sending the form twice. This story signs in itself (play); the app gives up on the request after 5 seconds. */
export const SigningIn: Story = {
  beforeEach: () => {
    fakeLogin(async () => {
      await delay("infinite");
      return HttpResponse.json(member);
    });
  },
  play: async ({ canvasElement }) => {
    const page = await signIn(canvasElement);
    await expect(
      await page.findByRole("button", { name: "Signing in" }),
    ).toBeDisabled();
  },
};

/** As a job seeker whose session ended on another device, I want the sign in page to say so and to take me back where I was, so that I understand why I have to sign in again. */
export const SessionEnded: Story = {
  render: () => <AppAt path="/jobs" />,
  beforeEach: () => {
    fakeSession(member);
    // The jobs list is the first call to find the session gone.
    server.use(http.get("*/api/jobs", () => unauthorized()));
  },
};

/** As a job seeker already signed in, I want the sign in address to take me straight to my jobs, so that I never sign in twice. */
export const AlreadySignedIn: Story = {
  beforeEach: () => {
    fakeSession(member);
  },
};

/** As an invited friend on my phone, I want the card to fill the screen's width with fields big enough to tap, so that I can sign in from the link I was sent. */
export const OnAPhone: Story = {
  globals: { viewport: { value: "mobile2", isRotated: false } },
  beforeEach: () => {
    fakeLogin(() => HttpResponse.json(member));
  },
};
