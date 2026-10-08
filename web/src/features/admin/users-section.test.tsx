import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { AdminUser } from "./api";
import { LAST_ADMIN_NOTE } from "./users-section";

const HOUR = 3_600_000;
const LAST_ADMIN =
  "This is the last active admin. Make someone else an admin first, then try again.";

const me: AdminUser = { ...admin, is_active: true, sessions: 2 };

let serial = 100;
function makeUser(overrides: Partial<AdminUser> = {}): AdminUser {
  serial += 1;
  return {
    id: `00000000-0000-4000-8000-${String(serial).padStart(12, "0")}`,
    email: `user${serial}@example.org`,
    name: `User ${serial}`,
    role: "member",
    is_active: true,
    created_at: new Date().toISOString(),
    last_login_at: new Date(Date.now() - 3 * HOUR).toISOString(),
    sessions: 1,
    ...overrides,
  };
}

/** A fake /api/admin/users over an in-memory list, with the invites section kept empty. */
function fakeUsers(
  initial: AdminUser[],
  options: {
    list?: (
      offset: number,
    ) => Response | undefined | Promise<Response | undefined>;
    patch?: (
      id: string,
      body: Record<string, unknown>,
    ) => Response | undefined | Promise<Response | undefined>;
  } = {},
) {
  const state = {
    items: initial.map((u) => ({ ...u })),
    lists: [] as number[],
    patches: [] as Array<{ id: string; body: Record<string, unknown> }>,
    headers: [] as Headers[],
  };
  const activeAdmins = () =>
    state.items.filter((u) => u.is_active && u.role === "admin").length;
  server.use(
    http.get("*/api/admin/invites", () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
    http.get("*/api/admin/users", async ({ request }) => {
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
        active_admins: activeAdmins(),
      });
    }),
    http.patch("*/api/admin/users/:id", async ({ params, request }) => {
      const id = String(params.id);
      const body = (await request.json()) as Record<string, unknown>;
      state.patches.push({ id, body });
      state.headers.push(request.headers);
      const override = await options.patch?.(id, body);
      if (override) return override;
      const found = state.items.find((u) => u.id === id);
      if (!found)
        return HttpResponse.json(
          { detail: "No user has this id." },
          { status: 404 },
        );
      const next = { ...found, ...body };
      const wasAdmin = found.is_active && found.role === "admin";
      const staysAdmin = next.is_active && next.role === "admin";
      if (wasAdmin && !staysAdmin && activeAdmins() <= 1)
        return HttpResponse.json({ detail: LAST_ADMIN }, { status: 409 });
      if (!next.is_active) next.sessions = 0;
      Object.assign(found, next);
      return HttpResponse.json(found);
    }),
  );
  return state;
}

const accounts = () =>
  screen.findByRole("table", { name: "Accounts, by name" });

async function rowOf(name: string) {
  const table = await accounts();
  const row = within(table)
    .getAllByRole("row")
    .find((r) => r.textContent.includes(name));
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

async function choose(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
  item: string,
) {
  await user.click(
    await screen.findByRole("button", { name: `Actions for ${name}` }),
  );
  await user.click(await screen.findByRole("menuitem", { name: item }));
  return screen.findByRole("alertdialog");
}

const noDashes = (text: string | null) => {
  expect(text ?? "").not.toMatch(/[–—]| - /);
};

describe("access", () => {
  it("a member never sees the page and the accounts are never asked for", async () => {
    fakeSession(member);
    const state = fakeUsers([me]);
    renderApp("/admin/users");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Accounts")).not.toBeInTheDocument();
    await delay(50);
    expect(state.lists).toEqual([]);
  });
});

describe("accounts table", () => {
  it("shows loading, then every account above the invites", async () => {
    fakeSession(admin);
    const zoe = makeUser({
      name: "Zoë Ångström",
      email: "zoe@example.org",
      sessions: 3,
    });
    const off = makeUser({
      name: "Old Friend",
      is_active: false,
      sessions: 0,
      last_login_at: null,
    });
    const olga = makeUser({ name: "Olga", role: "admin" });
    fakeUsers([me, off, olga, zoe], {
      list: async () => {
        await delay(150);
        return undefined;
      },
    });
    const { container } = renderApp("/admin/users");
    expect(await screen.findByText("Loading accounts")).toBeInTheDocument();
    const table = await accounts();

    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual(["Accounts", "Invites"]);
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual([
      "Name",
      "Role",
      "Status",
      "Last sign in",
      "Sessions",
      "Actions",
    ]);

    const mine = await rowOf("Harald Hautin");
    expect(within(mine).getByText("You")).toBeInTheDocument();
    expect(mine).toHaveTextContent("harald@example.org");
    expect(mine).toHaveTextContent("Admin");
    expect(mine).toHaveTextContent("Active");
    expect(mine).toHaveTextContent("Never");
    expect(mine).toHaveTextContent("2");

    const zoeRow = await rowOf("Zoë Ångström");
    expect(within(zoeRow).queryByText("You")).not.toBeInTheDocument();
    expect(zoeRow).toHaveTextContent("Member");
    expect(zoeRow).toHaveTextContent("3 hours ago");
    expect(zoeRow).toHaveTextContent("3");
    expect(zoeRow).not.toHaveAttribute("data-disabled");

    const offRow = await rowOf("Old Friend");
    expect(offRow).toHaveAttribute("data-disabled", "true");
    expect(offRow).toHaveTextContent("Disabled");
    expect(offRow).toHaveTextContent("Never");

    expect(await rowOf("Olga")).toHaveTextContent("Admin");
    noDashes(container.textContent);
  });

  it("shows an empty state", async () => {
    fakeSession(admin);
    fakeUsers([]);
    renderApp("/admin/users");
    expect(await screen.findByText("No accounts yet")).toBeInTheDocument();
  });

  it("shows an error with a retry that works", async () => {
    fakeSession(admin);
    let fail = true;
    fakeUsers([me], {
      list: () =>
        fail
          ? HttpResponse.json({ detail: "Database down." }, { status: 503 })
          : undefined,
    });
    const user = userEvent.setup();
    renderApp("/admin/users");
    expect(
      await screen.findByText(/The accounts could not be loaded\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/Database down\./)).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await rowOf("Harald Hautin")).toBeInTheDocument();
  });

  it("pages through many accounts", async () => {
    fakeSession(admin);
    const many = Array.from({ length: 60 }, (_, i) =>
      makeUser({ name: `Person ${String(i).padStart(2, "0")}` }),
    );
    const state = fakeUsers([me, ...many]);
    const user = userEvent.setup();
    renderApp("/admin/users");
    expect(await screen.findByText("1 to 50 of 61")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Accounts pages" });
    expect(
      within(nav).getByRole("button", { name: "Previous" }),
    ).toBeDisabled();
    await user.click(within(nav).getByRole("button", { name: "Next" }));
    expect(await screen.findByText("51 to 61 of 61")).toBeInTheDocument();
    expect(state.lists).toContain(50);
    expect(within(nav).getByRole("button", { name: "Next" })).toBeDisabled();
  });
});

describe("actions", () => {
  const cases = [
    {
      item: "Make admin",
      start: {},
      title: "Make Mia an admin?",
      confirm: "Make admin",
      body: { role: "admin" },
      toast: "Mia is now an admin.",
      after: "Admin",
    },
    {
      item: "Make member",
      start: { role: "admin" as const },
      title: "Make Mia a member?",
      confirm: "Make member",
      body: { role: "member" },
      toast: "Mia is now a member.",
      after: "Member",
    },
    {
      item: "Disable",
      start: {},
      title: "Disable Mia?",
      confirm: "Disable",
      body: { is_active: false },
      toast: "Mia is disabled and signed out everywhere.",
      after: "Disabled",
    },
    {
      item: "Enable",
      start: { is_active: false, sessions: 0 },
      title: "Enable Mia?",
      confirm: "Enable",
      body: { is_active: true },
      toast: "Mia can sign in again.",
      after: "Active",
    },
  ];

  it.each(cases)(
    "$item: cancel sends nothing, confirm sends the change",
    async ({ item, start, title, confirm, body, toast, after }) => {
      fakeSession(admin);
      const mia = makeUser({ name: "Mia", ...start });
      const state = fakeUsers([me, mia]);
      const user = userEvent.setup();
      renderApp("/admin/users");

      let dialog = await choose(user, "Mia", item);
      expect(
        within(dialog).getByRole("heading", { name: title }),
      ).toBeInTheDocument();
      noDashes(dialog.textContent);
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      });
      expect(state.patches).toEqual([]);

      dialog = await choose(user, "Mia", item);
      await user.click(within(dialog).getByRole("button", { name: confirm }));
      expect(await screen.findByText(toast)).toBeInTheDocument();
      expect(state.patches).toEqual([{ id: mia.id, body }]);
      expect(state.headers[0]?.get("X-Requested-With")).toBe("jobapp");
      await waitFor(async () => {
        expect(await rowOf("Mia")).toHaveTextContent(after);
      });
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    },
  );

  it("offers Make admin or Make member, and Disable or Enable, by state", async () => {
    fakeSession(admin);
    fakeUsers([
      me,
      makeUser({ name: "Off", is_active: false }),
      makeUser({ name: "Olga", role: "admin" }),
    ]);
    const user = userEvent.setup();
    renderApp("/admin/users");
    await user.click(
      await screen.findByRole("button", { name: "Actions for Off" }),
    );
    let items = screen.getAllByRole("menuitem").map((i) => i.textContent);
    expect(items).toEqual(["Make admin", "Enable"]);
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Actions for Olga" }));
    items = screen.getAllByRole("menuitem").map((i) => i.textContent);
    expect(items).toEqual(["Make member", "Disable"]);
    await user.keyboard("{Escape}");
    // Your own row: no Disable (another admin does that), demoting allowed with two admins.
    await user.click(
      screen.getByRole("button", { name: "Actions for Harald Hautin" }),
    );
    const own = screen.getAllByRole("menuitem");
    expect(own.map((i) => i.textContent)).toEqual(["Make member"]);
    expect(own[0]).not.toHaveAttribute("aria-disabled");
  });

  it("protects the last active admin in the menu", async () => {
    fakeSession(admin);
    fakeUsers([
      me,
      makeUser({ name: "Mia" }),
      makeUser({ name: "Off admin", role: "admin", is_active: false }),
    ]);
    const user = userEvent.setup();
    renderApp("/admin/users");
    await user.click(
      await screen.findByRole("button", { name: "Actions for Harald Hautin" }),
    );
    expect(
      screen.getByRole("menuitem", { name: "Make member" }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(LAST_ADMIN_NOTE)).toBeInTheDocument();
    noDashes(LAST_ADMIN_NOTE);
    await user.keyboard("{Escape}");
    // A disabled admin is not protected: it can be demoted or enabled.
    await user.click(
      screen.getByRole("button", { name: "Actions for Off admin" }),
    );
    expect(
      screen.getByRole("menuitem", { name: "Make member" }),
    ).not.toHaveAttribute("aria-disabled");
    expect(screen.queryByText(LAST_ADMIN_NOTE)).not.toBeInTheDocument();
  });

  it("shows the api's last admin refusal in the dialog and refreshes the list", async () => {
    fakeSession(admin);
    const olga = makeUser({ name: "Olga", role: "admin" });
    // The list said two admins, but Olga was disabled elsewhere meanwhile.
    const state = fakeUsers([me, olga], {
      patch: () => HttpResponse.json({ detail: LAST_ADMIN }, { status: 409 }),
    });
    const user = userEvent.setup();
    renderApp("/admin/users");
    const dialog = await choose(user, "Harald Hautin", "Make member");
    expect(
      within(dialog).getByRole("heading", { name: "Give up your admin role?" }),
    ).toBeInTheDocument();
    const lists = state.lists.length;
    await user.click(
      within(dialog).getByRole("button", { name: "Make me a member" }),
    );
    expect(await within(dialog).findByText(LAST_ADMIN)).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await waitFor(() => {
      expect(state.lists.length).toBeGreaterThan(lists);
    });
    // Still an admin, still here (behind the open dialog).
    expect(
      screen.getByRole("link", { name: "Admin", hidden: true }),
    ).toBeInTheDocument();
  });

  it("an account removed meanwhile closes the dialog with a toast", async () => {
    fakeSession(admin);
    fakeUsers([me, makeUser({ name: "Mia" })], {
      patch: () =>
        HttpResponse.json({ detail: "No user has this id." }, { status: 404 }),
    });
    const user = userEvent.setup();
    renderApp("/admin/users");
    const dialog = await choose(user, "Mia", "Disable");
    await user.click(within(dialog).getByRole("button", { name: "Disable" }));
    expect(
      await screen.findByText("This account no longer exists."),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  it("a server that cannot be reached keeps the dialog open with the reason", async () => {
    fakeSession(admin);
    fakeUsers([me, makeUser({ name: "Mia" })], {
      patch: () => HttpResponse.error(),
    });
    const user = userEvent.setup();
    renderApp("/admin/users");
    const dialog = await choose(user, "Mia", "Make admin");
    await user.click(
      within(dialog).getByRole("button", { name: "Make admin" }),
    );
    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("demoting yourself refreshes who you are and the Admin entry disappears", async () => {
    const session = fakeSession(admin);
    const olga = makeUser({ name: "Olga", role: "admin" });
    const state = fakeUsers([me, olga], {
      patch: (id, body) => {
        if (id === me.id && body.role === "member")
          session.user = { ...admin, role: "member" };
        return undefined;
      },
    });
    const user = userEvent.setup();
    const { location } = renderApp("/admin/users");
    expect(screen.queryByRole("link", { name: "Admin" })).toBeNull();
    expect(
      await screen.findByRole("link", { name: "Admin" }),
    ).toBeInTheDocument();

    const dialog = await choose(user, "Harald Hautin", "Make member");
    expect(dialog).toHaveTextContent("You lose the admin area at once");
    await user.click(
      within(dialog).getByRole("button", { name: "Make me a member" }),
    );
    expect(
      await screen.findByText("You are now a member."),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(location()).toBe("/");
    });
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "Admin" })).toBeNull();
    });
    expect(
      await screen.findByRole("heading", { name: /Welcome/ }),
    ).toBeInTheDocument();
    expect(state.patches).toEqual([{ id: me.id, body: { role: "member" } }]);
  });
});
