import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { PASSWORD_CHANGED } from "./password-card";

const CURRENT = "my current passphrase";
const NEXT = "a brand new passphrase";

type Answer = (body: Record<string, unknown>) => Response | Promise<Response>;

/** Signed in as `user`; records the password and rename calls with their headers. */
function account(
  options: { password?: Answer; rename?: Answer; user?: typeof admin } = {},
) {
  const session = fakeSession(options.user ?? admin);
  const calls = {
    password: [] as Array<Record<string, unknown>>,
    rename: [] as Array<Record<string, unknown>>,
    headers: [] as Headers[],
  };
  server.use(
    http.post("*/api/auth/password", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.password.push(body);
      calls.headers.push(request.headers);
      return options.password
        ? options.password(body)
        : new HttpResponse(null, { status: 204 });
    }),
    http.patch("*/api/auth/me", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.rename.push(body);
      calls.headers.push(request.headers);
      if (options.rename) return options.rename(body);
      const saved = { ...session.user!, name: String(body.name) };
      session.user = saved;
      return HttpResponse.json(saved);
    }),
  );
  return { session, calls };
}

const passwordForm = () =>
  screen.getByRole("form", { name: "Change password" });
const nameForm = () => screen.getByRole("form", { name: "Profile name" });

async function openAccount(path = "/account") {
  const utils = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Account" });
  return { ...utils, user: userEvent.setup() };
}

async function fillPassword(
  user: ReturnType<typeof userEvent.setup>,
  {
    current = CURRENT,
    next = NEXT,
    confirm = next,
  }: { current?: string; next?: string; confirm?: string } = {},
) {
  const form = within(passwordForm());
  if (current)
    await user.type(form.getByLabelText("Current password"), current);
  if (next) await user.type(form.getByLabelText("New password"), next);
  if (confirm)
    await user.type(form.getByLabelText("Confirm new password"), confirm);
}

const submitPassword = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(
    within(passwordForm()).getByRole("button", { name: "Change password" }),
  );

describe("account page", () => {
  it("opens from the user menu and shows both cards", async () => {
    account();
    const { location, user } = await (async () => {
      const utils = renderApp("/");
      await screen.findByRole("heading", { level: 1, name: "Jobs" });
      return { ...utils, user: userEvent.setup() };
    })();
    await user.click(
      screen.getByRole("button", { name: /Account menu, Harald Hautin/ }),
    );
    await user.click(await screen.findByRole("menuitem", { name: "Account" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Account" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/account");
    expect(document.title).toBe("Account · jobapp");
    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual([
      "Profile name",
      "Change password",
    ]);
  });

  it("has no dashes in its copy", async () => {
    account();
    const { container } = await openAccount();
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });

  it("asks a signed out visitor to sign in first", async () => {
    fakeSession(null);
    const { location } = renderApp("/account");
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/login?next=/account");
  });

  it("works for a member too", async () => {
    account({ user: member });
    await openAccount();
    expect(within(nameForm()).getByLabelText("Name")).toHaveValue(
      "Zoë Ångström",
    );
  });
});

describe("change password card", () => {
  it("shows the three fields with autocomplete hints and the rules hint", async () => {
    account();
    await openAccount();
    const form = within(passwordForm());
    expect(form.getByLabelText("Current password")).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
    expect(form.getByLabelText("New password")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(form.getByLabelText("New password")).toHaveAccessibleDescription(
      "Use 10 to 128 characters. It cannot be your email address or a common password.",
    );
    expect(form.getByLabelText("Confirm new password")).toHaveAttribute(
      "type",
      "password",
    );
  });

  it("toggles each field between hidden and shown", async () => {
    account();
    const { user } = await openAccount();
    const form = within(passwordForm());
    const input = form.getByLabelText("New password");
    const toggle = form.getByRole("button", { name: "Show new password" });
    expect(input).toHaveAttribute("type", "password");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    // The others stay hidden.
    expect(form.getByLabelText("Current password")).toHaveAttribute(
      "type",
      "password",
    );
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "password");
  });

  it("after a 429 shows the message and waits out Retry-After with a countdown", async () => {
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["setInterval", "clearInterval", "Date"],
    });
    try {
      const detail =
        "Too many attempts with a wrong current password. Try again in 15 minutes.";
      const { calls } = account({
        password: () =>
          HttpResponse.json(
            { detail },
            { status: 429, headers: { "Retry-After": "900" } },
          ),
      });
      const { user } = await openAccount();
      await fillPassword(user);
      await submitPassword(user);
      const form = within(passwordForm());
      expect(await form.findByRole("alert")).toHaveTextContent(detail);
      const locked = form.getByRole("button", { name: /try again in/i });
      expect(locked).toBeDisabled();
      expect(locked).toHaveTextContent("Try again in 15:00");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(899_000);
      });
      expect(locked).toHaveTextContent("Try again in 0:01");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(
        form.getByRole("button", { name: "Change password" }),
      ).toBeEnabled();
      expect(form.queryByRole("alert")).not.toBeInTheDocument();
      expect(calls.password).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("checks an empty form on the client and focuses the first error", async () => {
    const { calls } = account();
    const { user } = await openAccount();
    await submitPassword(user);
    const form = within(passwordForm());
    expect(
      await form.findByText("Enter your current password."),
    ).toBeInTheDocument();
    expect(form.getByText("Choose a password.")).toBeInTheDocument();
    expect(form.getByText("Repeat the new password.")).toBeInTheDocument();
    expect(form.getByLabelText("Current password")).toHaveFocus();
    expect(form.getByLabelText("Current password")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(calls.password).toEqual([]);
  });

  it("refuses a confirmation that does not match, without calling the api", async () => {
    const { calls } = account();
    const { user } = await openAccount();
    await fillPassword(user, { confirm: `${NEXT}!` });
    await submitPassword(user);
    const form = within(passwordForm());
    expect(
      await form.findByText("The passwords do not match."),
    ).toBeInTheDocument();
    expect(form.getByLabelText("Confirm new password")).toHaveFocus();
    expect(calls.password).toEqual([]);
  });

  it.each([
    ["short", "Use at least 10 characters for the password."],
    ["x".repeat(129), "Use at most 128 characters for the password."],
    ["HARALD@example.org", "The password cannot be your email address."],
    [CURRENT, "The new password must be different from the current one."],
  ])("checks the new password %# on the client", async (next, text) => {
    const { calls } = account();
    const { user } = await openAccount();
    const form = within(passwordForm());
    await user.type(form.getByLabelText("Current password"), CURRENT);
    // Paste: typing 129 characters one by one is slow and proves nothing more.
    await user.click(form.getByLabelText("New password"));
    await user.paste(next);
    await user.click(form.getByLabelText("Confirm new password"));
    await user.paste(next);
    await submitPassword(user);
    expect(await form.findByText(text)).toBeInTheDocument();
    expect(form.getByLabelText("New password")).toHaveFocus();
    expect(calls.password).toEqual([]);
  });

  it("sends both passwords with the CSRF header, shows the pending state, then confirms and clears", async () => {
    const { calls } = account({
      password: async () => {
        await delay(150);
        return new HttpResponse(null, { status: 204 });
      },
    });
    const { user, location } = await openAccount();
    await fillPassword(user);
    await submitPassword(user);
    const busy = within(passwordForm()).getByRole("button", {
      name: "Changing password",
    });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByText(PASSWORD_CHANGED)).toBeInTheDocument();
    expect(calls.password).toEqual([
      { current_password: CURRENT, new_password: NEXT },
    ]);
    expect(calls.headers[0]!.get("x-requested-with")).toBe("jobapp");
    const form = within(passwordForm());
    expect(form.getByLabelText("Current password")).toHaveValue("");
    expect(form.getByLabelText("New password")).toHaveValue("");
    expect(form.getByLabelText("Confirm new password")).toHaveValue("");
    expect(location()).toBe("/account");
    expect(form.getByRole("button", { name: "Change password" })).toBeEnabled();
  });

  it("puts a wrong current password under its field and focuses it", async () => {
    account({
      password: () =>
        HttpResponse.json(
          { detail: "current_password: The current password is incorrect." },
          { status: 400 },
        ),
    });
    const { user, location } = await openAccount();
    await fillPassword(user);
    await submitPassword(user);
    const form = within(passwordForm());
    expect(
      await form.findByText("The current password is incorrect."),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(form.getByLabelText("Current password")).toHaveFocus();
    });
    expect(location()).toBe("/account"); // still signed in
    expect(form.getByLabelText("New password")).toHaveValue(NEXT);
  });

  it("puts the api's rule message under the new password", async () => {
    account({
      password: () =>
        HttpResponse.json(
          {
            detail:
              "new_password: This password is too common. Choose one that is harder to guess.",
          },
          { status: 422 },
        ),
    });
    const { user } = await openAccount();
    await fillPassword(user, { next: "password12345" });
    await submitPassword(user);
    const form = within(passwordForm());
    expect(
      await form.findByText(
        "This password is too common. Choose one that is harder to guess.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(form.getByLabelText("New password")).toHaveFocus();
    });
  });

  it("shows any other failure above the fields", async () => {
    account({
      password: () =>
        HttpResponse.json({ detail: "internal server error" }, { status: 500 }),
    });
    const { user } = await openAccount();
    await fillPassword(user);
    await submitPassword(user);
    expect(await within(passwordForm()).findByRole("alert")).toHaveTextContent(
      "internal server error",
    );
  });

  it("says when the server cannot be reached", async () => {
    account({ password: () => HttpResponse.error() });
    const { user } = await openAccount();
    await fillPassword(user);
    await submitPassword(user);
    expect(await within(passwordForm()).findByRole("alert")).toHaveTextContent(
      "The server could not be reached.",
    );
  });

  it("goes to the sign in page when the session ended meanwhile", async () => {
    const { session } = account({
      password: () => {
        session.user = null;
        return HttpResponse.json(
          { detail: "Sign in to continue." },
          { status: 401 },
        );
      },
    });
    const { user, location } = await openAccount();
    await fillPassword(user);
    await submitPassword(user);
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toMatch(/^\/login/);
  });
});

describe("profile name card", () => {
  it("starts with the current name and the email, Save disabled until it changes", async () => {
    account();
    const { user } = await openAccount();
    const form = within(nameForm());
    const input = form.getByLabelText("Name");
    expect(input).toHaveValue("Harald Hautin");
    expect(form.getByText("harald@example.org")).toBeInTheDocument();
    const save = form.getByRole("button", { name: "Save name" });
    expect(save).toBeDisabled();
    await user.type(input, "  ");
    expect(save).toBeDisabled(); // trailing spaces are the same name
    await user.type(input, "x");
    expect(save).toBeEnabled();
  });

  it.each([
    ["", "Enter your name."],
    ["   ", "Enter your name."],
  ])("refuses %j on the client", async (name, text) => {
    const { calls } = account();
    const { user } = await openAccount();
    const input = within(nameForm()).getByLabelText("Name");
    await user.clear(input);
    if (name) await user.type(input, name);
    await user.click(
      within(nameForm()).getByRole("button", { name: "Save name" }),
    );
    expect(await within(nameForm()).findByText(text)).toBeInTheDocument();
    expect(input).toHaveFocus();
    expect(calls.rename).toEqual([]);
  });

  it("refuses more than 100 characters on the client", async () => {
    const { calls } = account();
    const { user } = await openAccount();
    const input = within(nameForm()).getByLabelText("Name");
    await user.clear(input);
    await user.click(input);
    await user.paste("é".repeat(101));
    await user.click(
      within(nameForm()).getByRole("button", { name: "Save name" }),
    );
    expect(
      await within(nameForm()).findByText(
        "Use at most 100 characters for the name.",
      ),
    ).toBeInTheDocument();
    expect(calls.rename).toEqual([]);
  });

  it("saves the cleaned name and the header menu shows it at once", async () => {
    const { calls } = account({
      rename: async (body) => {
        await delay(100);
        return HttpResponse.json({ ...admin, name: String(body.name) });
      },
    });
    const { user } = await openAccount();
    const input = within(nameForm()).getByLabelText("Name");
    await user.clear(input);
    await user.type(input, "  Zoë   Ångström ");
    await user.click(
      within(nameForm()).getByRole("button", { name: "Save name" }),
    );
    expect(
      within(nameForm()).getByRole("button", { name: "Saving" }),
    ).toBeDisabled();
    expect(await screen.findByText("Name saved.")).toBeInTheDocument();
    expect(calls.rename).toEqual([{ name: "Zoë Ångström" }]);
    expect(calls.headers[0]!.get("x-requested-with")).toBe("jobapp");
    expect(
      screen.getByRole("button", { name: /Account menu, Zoë Ångström/ }),
    ).toBeInTheDocument();
    expect(within(nameForm()).getByLabelText("Name")).toHaveValue(
      "Zoë Ångström",
    );
  });

  it("puts the api's name message under the field", async () => {
    account({
      rename: () =>
        HttpResponse.json(
          {
            detail: "name: The name contains characters that are not valid.",
          },
          { status: 422 },
        ),
    });
    const { user } = await openAccount();
    const input = within(nameForm()).getByLabelText("Name");
    await user.type(input, " Jr");
    await user.click(
      within(nameForm()).getByRole("button", { name: "Save name" }),
    );
    expect(
      await within(nameForm()).findByText(
        "The name contains characters that are not valid.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(input).toHaveFocus();
    });
  });

  it("shows any other failure above the field", async () => {
    account({
      rename: () =>
        HttpResponse.json({ detail: "internal server error" }, { status: 500 }),
    });
    const { user } = await openAccount();
    await user.type(within(nameForm()).getByLabelText("Name"), " Jr");
    await user.click(
      within(nameForm()).getByRole("button", { name: "Save name" }),
    );
    expect(await within(nameForm()).findByRole("alert")).toHaveTextContent(
      "internal server error",
    );
  });
});
