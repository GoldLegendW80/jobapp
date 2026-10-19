import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { AdminInvite } from "./api";

const TOKEN = "AbC_dEf-123".padEnd(43, "x");
const HOUR = 3_600_000;

let serial = 0;
function makeInvite(overrides: Partial<AdminInvite> = {}): AdminInvite {
  serial += 1;
  const now = Date.now();
  return {
    id: `00000000-0000-4000-8000-${String(serial).padStart(12, "0")}`,
    email: `friend${serial}@example.org`,
    kind: "invite",
    role: "member",
    status: "active",
    created_by_name: "Harald Example",
    created_at: new Date(now).toISOString(),
    expires_at: new Date(now + 7 * 24 * HOUR + 60_000).toISOString(),
    used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

/** A fake /api/admin/invites over an in-memory list, recording what the page sends. */
function fakeInvites(
  initial: AdminInvite[] = [],
  options: {
    list?: (
      offset: number,
    ) => Response | undefined | Promise<Response | undefined>;
    create?: (
      body: Record<string, unknown>,
    ) => Response | undefined | Promise<Response | undefined>;
    revoke?: (
      id: string,
    ) => Response | undefined | Promise<Response | undefined>;
  } = {},
) {
  const state = {
    items: [...initial],
    lists: [] as number[],
    creates: [] as Array<Record<string, unknown>>,
    revokes: [] as string[],
    headers: [] as Headers[],
  };
  server.use(
    http.get("*/api/admin/invites", async ({ request }) => {
      const url = new URL(request.url);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 50);
      state.lists.push(offset);
      const override = await options.list?.(offset);
      if (override) return override;
      return HttpResponse.json({
        items: state.items.slice(offset, offset + limit),
        total: state.items.length,
        limit,
        offset,
      });
    }),
    http.post("*/api/admin/invites", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      state.creates.push(body);
      state.headers.push(request.headers);
      const override = await options.create?.(body);
      if (override) return override;
      const kind = body.kind as "invite" | "reset";
      const invite = makeInvite({
        email: String(body.email).trim().toLowerCase(),
        kind,
        role: kind === "invite" ? (body.role as "member" | "admin") : null,
        expires_at: new Date(
          Date.now() + (kind === "invite" ? 7 * 24 * HOUR : HOUR),
        ).toISOString(),
      });
      state.items.unshift(invite);
      return HttpResponse.json(
        { invite, path: `/${kind}/${TOKEN}` },
        { status: 201 },
      );
    }),
    http.delete("*/api/admin/invites/:id", async ({ params, request }) => {
      const id = String(params.id);
      state.revokes.push(id);
      state.headers.push(request.headers);
      const override = await options.revoke?.(id);
      if (override) return override;
      const found = state.items.find((i) => i.id === id);
      if (!found || found.status !== "active")
        return HttpResponse.json(
          { detail: "No active invite has this id." },
          { status: 404 },
        );
      found.status = "revoked";
      found.revoked_at = new Date().toISOString();
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}

function setClipboard(writeText: ((text: string) => Promise<void>) | null) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: writeText ? { writeText } : undefined,
  });
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: "Invite someone" }),
  );
  return screen.findByRole("dialog", { name: "Invite someone" });
}

const noDashes = (text: string | null) => {
  expect(text ?? "").not.toMatch(/[–—]| - /);
};

beforeEach(() => {
  // The accounts section above the invites: the admin alone (users-section.test.tsx covers it).
  server.use(
    http.get("*/api/admin/users", () =>
      HttpResponse.json({
        items: [{ ...admin, is_active: true, sessions: 1 }],
        total: 1,
        limit: 50,
        offset: 0,
        active_admins: 1,
      }),
    ),
  );
});

afterEach(() => {
  setClipboard(null);
});

describe("access", () => {
  it("a member gets the not found page and the invites are never asked for", async () => {
    fakeSession(member);
    const state = fakeInvites([makeInvite()]);
    renderApp("/admin/users");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Invites")).not.toBeInTheDocument();
    await delay(50);
    expect(state.lists).toEqual([]);
    expect(
      screen.queryByRole("link", { name: "Admin" }),
    ).not.toBeInTheDocument();
  });

  it("signed out, it goes to the sign in page", async () => {
    fakeSession(null);
    fakeInvites();
    const { location } = renderApp("/admin/users");
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/login?next=/admin/users");
  });
});

describe("invites list", () => {
  it("shows loading, then the empty state", async () => {
    fakeSession(admin);
    fakeInvites([], {
      list: async () => {
        await delay(150);
        return undefined;
      },
    });
    const { container } = renderApp("/admin/users");
    expect(await screen.findByText("Loading invites")).toBeInTheDocument();
    expect(await screen.findByText("No invites yet")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Users", level: 1 }),
    ).toBeInTheDocument();
    expect(document.title).toBe("Users · jobapp");
    noDashes(container.textContent);
  });

  it("shows each link with kind, role, status and expiry; revoke only while active", async () => {
    fakeSession(admin);
    const now = Date.now();
    fakeInvites([
      makeInvite({ email: "active@example.org", role: "admin" }),
      makeInvite({
        email: "reset@example.org",
        kind: "reset",
        role: null,
        expires_at: new Date(now + 40 * 60_000 + 5000).toISOString(),
      }),
      makeInvite({
        email: "used@example.org",
        status: "used",
        created_by_name: null,
        used_at: new Date(now - 3 * HOUR).toISOString(),
      }),
      makeInvite({
        email: "revoked@example.org",
        status: "revoked",
        revoked_at: new Date(now - 30_000).toISOString(),
      }),
      makeInvite({
        email: "expired@example.org",
        status: "expired",
        expires_at: new Date(now - 2 * 24 * HOUR - 1000).toISOString(),
      }),
    ]);
    const { container } = renderApp("/admin/users");
    const table = await screen.findByRole("table", {
      name: "Invites, newest first",
    });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(5);
    const row = (email: string) =>
      rows.find((r) => r.textContent.includes(email))!;

    const active = row("active@example.org");
    expect(active).toHaveTextContent("Invite");
    expect(active).toHaveTextContent("Admin");
    expect(active).toHaveTextContent("Active");
    expect(active).toHaveTextContent("in 7 days");
    expect(active).toHaveTextContent("by Harald Example");
    expect(
      within(active).getByRole("button", {
        name: "Revoke the link for active@example.org",
      }),
    ).toBeInTheDocument();

    const reset = row("reset@example.org");
    expect(reset).toHaveTextContent("Password reset");
    expect(reset).toHaveTextContent("Unchanged");
    expect(reset).toHaveTextContent("in 40 minutes");

    expect(row("used@example.org")).toHaveTextContent("Used");
    expect(row("used@example.org")).not.toHaveTextContent("by ");
    expect(row("used@example.org")).toHaveTextContent("Used 3 hours ago");
    expect(row("revoked@example.org")).toHaveTextContent("Revoked now");
    expect(row("expired@example.org")).toHaveTextContent("Expired");
    expect(row("expired@example.org")).toHaveTextContent("2 days ago");
    for (const email of ["used", "revoked", "expired"])
      expect(
        within(row(`${email}@example.org`)).queryByRole("button"),
      ).not.toBeInTheDocument();
    noDashes(container.textContent);
  });

  it("reports a failed load and retries", async () => {
    fakeSession(admin);
    let call = 0;
    fakeInvites([makeInvite({ email: "back@example.org" })], {
      list: () => {
        call += 1;
        return call === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : undefined;
      },
    });
    renderApp("/admin/users");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The invites could not be loaded.");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("back@example.org")).toBeInTheDocument();
  });

  it("says the server is unreachable on a network failure", async () => {
    fakeSession(admin);
    fakeInvites([], { list: () => HttpResponse.error() });
    renderApp("/admin/users");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server could not be reached.",
    );
  });

  it("pages through more than 20 links", async () => {
    fakeSession(admin);
    const items = Array.from({ length: 25 }, (_, n) =>
      makeInvite({ email: `p${n}@example.org` }),
    );
    const state = fakeInvites(items);
    renderApp("/admin/users");
    expect(await screen.findByText("p0@example.org")).toBeInTheDocument();
    expect(screen.getByText("1 to 20 of 25")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("p24@example.org")).toBeInTheDocument();
    expect(screen.getByText("21 to 25 of 25")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(state.lists).toEqual([0, 20]);
  });
});

describe("invite dialog", () => {
  it("validates the email on the client and sends nothing", async () => {
    fakeSession(admin);
    const state = fakeInvites();
    renderApp("/admin/users");
    const user = userEvent.setup();
    const dialog = await openDialog(user);
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );
    expect(
      await within(dialog).findByText("Enter an email address."),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Email")).toHaveFocus();
    await user.type(within(dialog).getByLabelText("Email"), "not an email");
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );
    expect(
      await within(dialog).findByText("Enter a valid email address."),
    ).toBeInTheDocument();
    expect(state.creates).toEqual([]);
    noDashes(dialog.textContent);
  });

  it("creates an invite with a role, shows the full link once, then forgets it", async () => {
    fakeSession(admin);
    const state = fakeInvites();
    renderApp("/admin/users");
    const user = userEvent.setup();
    let dialog = await openDialog(user);
    await user.type(
      within(dialog).getByLabelText("Email"),
      "  Friend@Example.org ",
    );
    await user.selectOptions(within(dialog).getByLabelText("Role"), "admin");
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );

    const result = await screen.findByRole("dialog", {
      name: "Invite link ready",
    });
    expect(state.creates).toEqual([
      { email: "Friend@Example.org", kind: "invite", role: "admin" },
    ]);
    expect(state.headers[0]!.get("x-requested-with")).toBe("jobapp");
    const link = within(result).getByLabelText("Invite link");
    expect(link).toHaveValue(`${window.location.origin}/invite/${TOKEN}`);
    expect(link).toHaveAttribute("readonly");
    expect(result).toHaveTextContent("shown only once");
    expect(result).toHaveTextContent("expires in 7 days");
    expect(result).toHaveTextContent("Send this link to friend@example.org");
    noDashes(result.textContent);

    await user.click(within(result).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    // The list behind refreshed with the new link, and the link itself is nowhere.
    expect(
      await screen.findByRole("table", { name: "Invites, newest first" }),
    ).toHaveTextContent("friend@example.org");
    expect(document.body.textContent).not.toContain(TOKEN);
    dialog = await openDialog(user);
    expect(within(dialog).getByLabelText("Email")).toHaveValue("");
    expect(
      screen.queryByDisplayValue(new RegExp(TOKEN)),
    ).not.toBeInTheDocument();
  });

  it("a reset link hides the role, sends none, and says 1 hour", async () => {
    fakeSession(admin);
    const state = fakeInvites();
    renderApp("/admin/users");
    const user = userEvent.setup();
    const dialog = await openDialog(user);
    expect(within(dialog).getByLabelText("Role")).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText("Link"), "reset");
    expect(within(dialog).queryByLabelText("Role")).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("for 1 hour");
    await user.type(
      within(dialog).getByLabelText("Email"),
      "friend@example.org",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );
    const result = await screen.findByRole("dialog", {
      name: "Reset link ready",
    });
    expect(state.creates).toEqual([
      { email: "friend@example.org", kind: "reset" },
    ]);
    expect(within(result).getByLabelText("Reset link")).toHaveValue(
      `${window.location.origin}/reset/${TOKEN}`,
    );
    expect(result).toHaveTextContent("expires in 1 hour");
  });

  it.each([
    [
      409,
      "An account with this email already exists. Send a reset link instead.",
    ],
    [404, "No active account uses this email."],
    [422, "Enter a valid email address."],
  ])(
    "puts the api's %d answer under the email field",
    async (status, detail) => {
      fakeSession(admin);
      fakeInvites([], {
        create: () => HttpResponse.json({ detail }, { status }),
      });
      renderApp("/admin/users");
      const user = userEvent.setup();
      const dialog = await openDialog(user);
      await user.type(
        within(dialog).getByLabelText("Email"),
        "taken@example.org",
      );
      await user.click(
        within(dialog).getByRole("button", { name: "Create link" }),
      );
      expect(await within(dialog).findByText(detail)).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Email")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
      expect(screen.queryByText("Invite link ready")).not.toBeInTheDocument();
    },
  );

  it("shows other failures in the dialog and keeps the form", async () => {
    fakeSession(admin);
    let call = 0;
    fakeInvites([], {
      create: () => {
        call += 1;
        return call === 1
          ? HttpResponse.json({ detail: "database down" }, { status: 503 })
          : HttpResponse.error();
      },
    });
    renderApp("/admin/users");
    const user = userEvent.setup();
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Email"), "f@example.org");
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "database down",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );
    await waitFor(() => {
      expect(within(dialog).getByRole("alert")).toHaveTextContent(
        "The server could not be reached.",
      );
    });
    expect(within(dialog).getByLabelText("Email")).toHaveValue("f@example.org");
  });

  it("disables the button while creating, so a double click makes one link", async () => {
    fakeSession(admin);
    const state = fakeInvites([], {
      create: async () => {
        await delay(150);
        return undefined;
      },
    });
    renderApp("/admin/users");
    const user = userEvent.setup();
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Email"), "f@example.org");
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );
    const busy = within(dialog).getByRole("button", { name: "Creating link" });
    expect(busy).toBeDisabled();
    await user.dblClick(busy);
    await screen.findByRole("dialog", { name: "Invite link ready" });
    expect(state.creates).toHaveLength(1);
  });

  async function createdLink(user: ReturnType<typeof userEvent.setup>) {
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText("Email"), "f@example.org");
    await user.click(
      within(dialog).getByRole("button", { name: "Create link" }),
    );
    return screen.findByRole("dialog", { name: "Invite link ready" });
  }

  it("copies the link to the clipboard", async () => {
    fakeSession(admin);
    fakeInvites();
    renderApp("/admin/users");
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    setClipboard(writeText);
    const result = await createdLink(user);
    await user.click(within(result).getByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/invite/${TOKEN}`,
    );
    expect(
      await within(result).findByRole("button", { name: "Copied" }),
    ).toBeInTheDocument();
    expect(within(result).getByRole("status")).toHaveTextContent(
      "The link is on your clipboard.",
    );
    expect(await screen.findByText("Link copied.")).toBeInTheDocument();
  });

  it.each([
    ["rejects", () => vi.fn(() => Promise.reject(new Error("denied")))],
    ["is missing", () => null],
  ])(
    "when the clipboard %s, it selects the link and says how to copy by hand",
    async (_, make) => {
      fakeSession(admin);
      fakeInvites();
      renderApp("/admin/users");
      const user = userEvent.setup();
      setClipboard(make());
      const result = await createdLink(user);
      await user.click(
        within(result).getByRole("button", { name: "Copy link" }),
      );
      expect(
        await within(result).findByText(/This browser blocked copying/),
      ).toBeInTheDocument();
      const input =
        within(result).getByLabelText<HTMLInputElement>("Invite link");
      expect(input).toHaveFocus();
      expect(input.selectionStart).toBe(0);
      expect(input.selectionEnd).toBe(input.value.length);
      expect(
        within(result).getByRole("button", { name: "Copy link" }),
      ).toBeInTheDocument();
    },
  );

  it("a click outside does not throw away a link on screen", async () => {
    fakeSession(admin);
    fakeInvites();
    renderApp("/admin/users");
    // The overlay sets pointer-events none on the body; click it anyway, as a stray tap would.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const result = await createdLink(user);
    await user.click(document.body);
    expect(result).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});

describe("revoke", () => {
  it("asks first; cancel sends nothing, confirm revokes and refreshes", async () => {
    fakeSession(admin);
    const state = fakeInvites([makeInvite({ email: "gone@example.org" })]);
    renderApp("/admin/users");
    const user = userEvent.setup();
    const button = await screen.findByRole("button", {
      name: "Revoke the link for gone@example.org",
    });
    await user.click(button);
    let confirm = await screen.findByRole("alertdialog", {
      name: "Revoke this link?",
    });
    expect(confirm).toHaveTextContent("gone@example.org");
    noDashes(confirm.textContent);
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(state.revokes).toEqual([]);

    await user.click(button);
    confirm = await screen.findByRole("alertdialog");
    await user.click(
      within(confirm).getByRole("button", { name: "Revoke link" }),
    );
    expect(
      await screen.findByText("Link revoked. It no longer works."),
    ).toBeInTheDocument();
    expect(state.revokes).toEqual([state.items[0]!.id]);
    expect(state.headers[0]!.get("x-requested-with")).toBe("jobapp");
    const row = await screen.findByRole("row", { name: /gone@example.org/ });
    await waitFor(() => {
      expect(row).toHaveTextContent("Revoked");
    });
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("a link that died meanwhile: 404 closes with a notice and refreshes", async () => {
    fakeSession(admin);
    const invite = makeInvite({ email: "late@example.org" });
    const state = fakeInvites([invite], {
      revoke: () => {
        invite.status = "used";
        return undefined;
      },
    });
    renderApp("/admin/users");
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: "Revoke the link for late@example.org",
      }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Revoke link",
      }),
    );
    expect(
      await screen.findByText(
        "This link was already used, revoked or expired.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.getByRole("row", { name: /late@example.org/ }),
      ).toHaveTextContent("Used");
    });
    expect(state.lists.length).toBeGreaterThanOrEqual(2);
  });

  it("another failure stays in the dialog so the admin can retry", async () => {
    fakeSession(admin);
    let call = 0;
    const state = fakeInvites([makeInvite({ email: "x@example.org" })], {
      revoke: () => {
        call += 1;
        return call === 1 ? HttpResponse.error() : undefined;
      },
    });
    renderApp("/admin/users");
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: /Revoke the link for/ }),
    );
    const confirm = await screen.findByRole("alertdialog");
    await user.click(
      within(confirm).getByRole("button", { name: "Revoke link" }),
    );
    expect(await within(confirm).findByRole("alert")).toHaveTextContent(
      "The server could not be reached.",
    );
    await user.click(
      within(confirm).getByRole("button", { name: "Revoke link" }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(state.revokes).toHaveLength(2);
    expect(
      await screen.findByRole("row", { name: /x@example.org/ }),
    ).toHaveTextContent("Revoked");
  });
});
