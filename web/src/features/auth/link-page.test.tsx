import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

// The api's shape: 43 URL-safe characters.
const TOKEN = "AbC_dEf-123".padEnd(43, "x");
const LINK_404 = {
  detail: "This link is not valid. It may have expired or been used already.",
};

function linkAnswers(
  check: () => Response | Promise<Response>,
  accept?: (body: Record<string, unknown>) => Response | Promise<Response>,
) {
  const session = fakeSession(null);
  const calls = {
    check: 0,
    accept: [] as Array<Record<string, unknown>>,
    headers: [] as Headers[],
  };
  server.use(
    http.get("*/api/auth/invites/:token", () => {
      calls.check += 1;
      return check();
    }),
    http.post("*/api/auth/invites/:token/accept", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.accept.push(body);
      calls.headers.push(request.headers);
      const response = accept ? await accept(body) : HttpResponse.json(admin);
      if (response.status === 200)
        session.user = (await response.clone().json()) as typeof admin;
      return response;
    }),
  );
  return calls;
}

const invite = () =>
  HttpResponse.json({
    email: "harald@example.org",
    kind: "invite",
    expires_at: "2026-10-15T10:00:00Z",
  });
const reset = () =>
  HttpResponse.json({
    email: "harald@example.org",
    kind: "reset",
    expires_at: "2026-10-08T11:00:00Z",
  });

const PASSWORD = "a long passphrase";

async function fill(
  user: ReturnType<typeof userEvent.setup>,
  {
    name,
    password = PASSWORD,
    confirm = password,
  }: { name?: string; password?: string; confirm?: string },
) {
  if (name !== undefined) await user.type(screen.getByLabelText("Name"), name);
  if (password)
    await user.type(screen.getByLabelText(/^(New )?password$/i), password);
  if (confirm)
    await user.type(screen.getByLabelText("Confirm password"), confirm);
}

describe("invite page", () => {
  it("shows a loading state while the link is checked", async () => {
    linkAnswers(async () => {
      await delay(150);
      return invite();
    });
    renderApp(`/invite/${TOKEN}`);
    expect(screen.getByRole("status")).toHaveTextContent("Checking your link");
    expect(
      screen.getByRole("heading", { name: "Set up your account" }),
    ).toBeInTheDocument();
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  });

  it("shows the email read only with name, password and confirm fields", async () => {
    const calls = linkAnswers(invite);
    const { container } = renderApp(`/invite/${TOKEN}`);
    const email = await screen.findByLabelText("Email");
    expect(email).toHaveValue("harald@example.org");
    expect(email).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
      "Use 10 to 128 characters. It cannot be your email address or a common password.",
    );
    expect(screen.getByLabelText("Confirm password")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create account" }),
    ).toBeEnabled();
    expect(calls.check).toBe(1);
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });

  it("sends name and password, then lands signed in on / with the user menu", async () => {
    const calls = linkAnswers(invite, () =>
      HttpResponse.json({ ...admin, name: "Zoë Ångström" }),
    );
    const { location } = renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "  Zoë   Ångström " });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByRole("heading", { name: "Welcome, Zoë Ångström" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/");
    expect(calls.accept).toEqual([
      { name: "Zoë Ångström", password: PASSWORD },
    ]);
    expect(calls.headers[0]!.get("x-requested-with")).toBe("jobapp");
    expect(
      screen.getByRole("button", { name: /Account menu, Zoë Ångström/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Welcome, Zoë Ångström."),
    ).toBeInTheDocument();
  });

  it("checks the confirmation on the client and sends nothing on a mismatch", async () => {
    const calls = linkAnswers(invite);
    renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "Zoë", confirm: "a long passphrasf" });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByText("The passwords do not match."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText("Confirm password")).toHaveFocus();
    expect(calls.accept).toHaveLength(0);
  });

  it("checks every field on the client and focuses the first error", async () => {
    const calls = linkAnswers(invite);
    renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Enter your name.")).toBeInTheDocument();
    expect(
      screen.getByText("Use at least 10 characters for the password."),
    ).toBeInTheDocument();
    expect(screen.getByText("Repeat the password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveFocus();
    expect(calls.accept).toHaveLength(0);
  });

  it("refuses the email as password on the client", async () => {
    const calls = linkAnswers(invite);
    renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "Zoë", password: "Harald@Example.org" });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByText("The password cannot be your email address."),
    ).toBeInTheDocument();
    expect(calls.accept).toHaveLength(0);
  });

  it("shows the api's rule message verbatim under the password on 422", async () => {
    const message =
      "This password is too common. Choose one that is harder to guess.";
    linkAnswers(invite, () =>
      HttpResponse.json({ detail: message }, { status: 422 }),
    );
    const { location } = renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "Zoë", password: "password123" });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("aria-invalid", "true");
    expect(password).toHaveFocus();
    expect(location()).toBe(`/invite/${TOKEN}`);
    expect(
      screen.getByRole("button", { name: "Create account" }),
    ).toBeEnabled();
  });

  it("puts a name rule message under the name", async () => {
    const message = "The name contains characters that are not valid.";
    linkAnswers(invite, () =>
      HttpResponse.json({ detail: message }, { status: 422 }),
    );
    renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "Zoë" });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("shows any other refusal in an alert (409 account exists, network)", async () => {
    let call = 0;
    linkAnswers(invite, () => {
      call += 1;
      return call === 1
        ? HttpResponse.json(
            { detail: "An account with this email already exists." },
            { status: 409 },
          )
        : HttpResponse.error();
    });
    renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "Zoë" });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An account with this email already exists.",
    );
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The server could not be reached.",
      );
    });
  });

  it("shows the invalid page when the link died between check and submit", async () => {
    linkAnswers(invite, () => HttpResponse.json(LINK_404, { status: 404 }));
    renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "Zoë" });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByRole("heading", {
        name: "This link is invalid or expired",
      }),
    ).toBeInTheDocument();
  });

  it("disables the button while saving", async () => {
    const calls = linkAnswers(invite, async () => {
      await delay(150);
      return HttpResponse.json(admin);
    });
    renderApp(`/invite/${TOKEN}`);
    const user = userEvent.setup();
    await screen.findByLabelText("Name");
    await fill(user, { name: "Zoë" });
    await user.click(screen.getByRole("button", { name: "Create account" }));
    const busy = screen.getByRole("button", { name: "Creating account" });
    expect(busy).toBeDisabled();
    await user.dblClick(busy);
    await screen.findByRole("heading", { name: /^Welcome/ });
    expect(calls.accept).toHaveLength(1);
  });
});

describe("reset page", () => {
  it("hides the name and sends the password only", async () => {
    const calls = linkAnswers(reset);
    const { location } = renderApp(`/reset/${TOKEN}`);
    expect(await screen.findByLabelText("New password")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Choose a new password" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    const user = userEvent.setup();
    await fill(user, {});
    await user.click(screen.getByRole("button", { name: "Save password" }));
    await screen.findByRole("heading", { name: /^Welcome/ });
    expect(calls.accept).toEqual([{ password: PASSWORD }]);
    expect(location()).toBe("/");
    expect(document.title).toBe("Home · jobapp");
  });

  it("follows the api: a reset link opened on /invite asks no name", async () => {
    linkAnswers(reset);
    renderApp(`/invite/${TOKEN}`);
    expect(
      await screen.findByRole("heading", { name: "Choose a new password" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  });
});

describe("dead and broken links", () => {
  it.each([
    ["/invite/", TOKEN],
    ["/reset/", TOKEN],
  ])(
    "%s%s answering 404 shows the friendly invalid page",
    async (prefix, token) => {
      linkAnswers(() => HttpResponse.json(LINK_404, { status: 404 }));
      const { container } = renderApp(`${prefix}${token}`);
      expect(
        await screen.findByRole("heading", {
          name: "This link is invalid or expired",
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: "Go to sign in" }),
      ).toHaveAttribute("href", "/login");
      expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
      expect(container.textContent).not.toMatch(/[–—]| - /);
    },
  );

  it.each([
    "short",
    "%2E%2E",
    "x".repeat(44),
    "x".repeat(500),
    `${"x".repeat(42)}%3F`,
    `${"x".repeat(42)}.`,
    `${"x".repeat(42)}é`,
  ])(
    "a malformed token %j shows the invalid page without asking the api",
    async (token) => {
      const calls = linkAnswers(invite);
      renderApp(`/invite/${token}`);
      expect(
        screen.getByRole("heading", {
          name: "This link is invalid or expired",
        }),
      ).toBeInTheDocument();
      await delay(50);
      expect(calls.check).toBe(0);
      expect(document.title).toBe("Invalid link · jobapp");
    },
  );

  it("offers a retry when the check cannot reach the server", async () => {
    let call = 0;
    linkAnswers(() => {
      call += 1;
      return call === 1 ? HttpResponse.error() : invite();
    });
    renderApp(`/invite/${TOKEN}`);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The server could not be reached.");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(within(document.body).queryByRole("alert")).not.toBeInTheDocument();
  });
});
