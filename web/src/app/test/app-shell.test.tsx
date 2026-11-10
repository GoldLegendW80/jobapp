import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { phoneScreen } from "@/test/media";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const TABS = ["Jobs", "Discover", "Applications", "Profile", "Activity"];

const mainNav = () => screen.getByRole("navigation", { name: "Main" });
const tabs = () =>
  within(mainNav())
    .getAllByRole("link")
    .map((link) => link.textContent);

async function openAccountMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Account menu/ }));
  return screen.findByRole("menu");
}

async function openJobs(path = "/jobs") {
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Jobs" });
  return app;
}

describe("the shell on a wide screen", () => {
  it("keeps the main navigation in the header, with no bottom bar", async () => {
    fakeSession(member);
    await openJobs();
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
    expect(within(screen.getByRole("banner")).getByRole("navigation")).toBe(
      mainNav(),
    );
    expect(tabs()).toEqual(TABS);
    expect(
      screen
        .getByRole("main")
        .parentElement?.style.getPropertyValue("--bottom-bar"),
    ).toBe("");
  });

  it("has Activity in the main navigation, not in the account menu, and Admin in the header", async () => {
    fakeSession(admin);
    const user = userEvent.setup();
    await openJobs();
    expect(
      within(mainNav()).getByRole("link", { name: "Admin" }),
    ).toHaveAttribute("href", "/admin/users");
    expect(
      within(mainNav()).getByRole("link", { name: "Activity" }),
    ).toHaveAttribute("href", "/activity");
    const menu = await openAccountMenu(user);
    expect(
      within(menu).queryByRole("menuitem", { name: "Activity" }),
    ).not.toBeInTheDocument();
    expect(
      within(menu).queryByRole("menuitem", { name: "Admin" }),
    ).not.toBeInTheDocument();
  });

  it("opens the workflow runs from Activity", async () => {
    fakeSession(member);
    const user = userEvent.setup();
    const { location } = await openJobs();
    await user.click(within(mainNav()).getByRole("link", { name: "Activity" }));
    await waitFor(() => {
      expect(location()).toBe("/activity");
    });
    expect(
      await screen.findByRole("heading", { level: 1, name: "Activity" }),
    ).toBeInTheDocument();
  });

  it("shows how many workflow runs are under way after Activity, in the link's name", async () => {
    fakeSession(member);
    server.use(
      http.get("*/api/activity/count", () =>
        HttpResponse.json({ under_way: 2 }),
      ),
    );
    await openJobs();
    const activity = await within(mainNav()).findByRole("link", {
      name: /^Activity\s*,\s*2 under way$/,
    });
    expect(activity).toHaveAttribute("href", "/activity");
    expect(within(activity).getByText("2")).toBeVisible();
  });

  it("shows no count on Activity when no run is under way", async () => {
    fakeSession(member);
    let read = false;
    server.use(
      http.get("*/api/activity/count", () => {
        read = true;
        return HttpResponse.json({ under_way: 0 });
      }),
    );
    await openJobs();
    await waitFor(() => {
      expect(read).toBe(true);
    });
    const activity = within(mainNav()).getByRole("link", { name: "Activity" });
    expect(activity).toHaveTextContent(/^Activity$/);
  });

  it("keeps Admin current on every admin page, not only the one it opens", async () => {
    fakeSession(admin);
    server.use(
      http.get("*/api/admin/agents", () => HttpResponse.json({ items: [] })),
    );
    renderApp("/admin/agents");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const link = within(nav).getByRole("link", { name: "Admin" });
    await waitFor(() => {
      expect(link).toHaveAttribute("aria-current", "page");
    });
    expect(within(nav).getByRole("link", { name: "Jobs" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});

describe("the shell on a phone", () => {
  it("shows a bottom tab bar with the four main pages, and no navigation in the header", async () => {
    phoneScreen();
    fakeSession(admin);
    await openJobs();
    const banner = screen.getByRole("banner");
    expect(within(banner).queryByRole("navigation")).not.toBeInTheDocument();
    expect(within(banner).getByRole("link", { name: "jobapp" })).toBeVisible();
    expect(
      within(banner).getByRole("button", { name: /^Account menu/ }),
    ).toBeVisible();
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
    expect(banner).not.toContainElement(mainNav());
    // Admin is in the account menu on a phone, never in the bar.
    expect(tabs()).toEqual(TABS);
  });

  it("marks the current page in the bar, and moves it on a tap", async () => {
    phoneScreen();
    fakeSession(member);
    const user = userEvent.setup();
    const { location } = await openJobs();
    const link = (name: string) =>
      within(mainNav()).getByRole("link", { name });
    expect(link("Jobs")).toHaveAttribute("aria-current", "page");
    for (const name of ["Discover", "Applications", "Profile"])
      expect(link(name)).not.toHaveAttribute("aria-current");
    await user.click(link("Profile"));
    await waitFor(() => {
      expect(location()).toBe("/profile");
    });
    // The bar moves when the Profile page has loaded (pages are lazy; the old page stays till then).
    await waitFor(() => {
      expect(link("Profile")).toHaveAttribute("aria-current", "page");
    });
    expect(link("Jobs")).not.toHaveAttribute("aria-current");
  });

  it("keeps the Applications count and the needs you dot in the bar, in the link's name", async () => {
    phoneScreen();
    fakeSession(member);
    server.use(
      http.get("*/api/apply/queue/count", () =>
        HttpResponse.json({ waiting_on_you: 3, needs_you: 1 }),
      ),
    );
    await openJobs();
    const applications = await within(mainNav()).findByRole("link", {
      name: /^Applications\s*,\s*3 waiting for you\s*,\s*1 needs you$/,
    });
    expect(applications).toHaveAttribute("href", "/applications");
    expect(within(applications).getByText("3")).toBeVisible();
    expect(
      within(applications).getByTestId("needs-you-dot"),
    ).toBeInTheDocument();
  });

  it("keeps the Activity count in the bar, in the link's name", async () => {
    phoneScreen();
    fakeSession(member);
    server.use(
      http.get("*/api/activity/count", () =>
        HttpResponse.json({ under_way: 1 }),
      ),
    );
    await openJobs();
    const activity = await within(mainNav()).findByRole("link", {
      name: /^Activity\s*,\s*1 under way$/,
    });
    expect(activity).toHaveAttribute("href", "/activity");
    expect(within(activity).getByText("1")).toBeVisible();
  });

  it("puts Activity in the bar and, for an admin, Admin in the account menu", async () => {
    phoneScreen();
    fakeSession(admin);
    const user = userEvent.setup();
    await openJobs();
    expect(
      within(mainNav()).getByRole("link", { name: "Activity" }),
    ).toHaveAttribute("href", "/activity");
    const menu = await openAccountMenu(user);
    expect(
      within(menu).queryByRole("menuitem", { name: "Activity" }),
    ).not.toBeInTheDocument();
    expect(
      within(menu).getByRole("menuitem", { name: "Admin" }),
    ).toHaveAttribute("href", "/admin/users");
  });

  it("shows no Admin to a member", async () => {
    phoneScreen();
    fakeSession(member);
    const user = userEvent.setup();
    await openJobs();
    const menu = await openAccountMenu(user);
    expect(
      within(menu).getByRole("menuitem", { name: "Account" }),
    ).toBeInTheDocument();
    expect(
      within(menu).queryByRole("menuitem", { name: "Admin" }),
    ).not.toBeInTheDocument();
  });

  it("reaches the skip link, the header, then the bar before the page", async () => {
    phoneScreen();
    fakeSession(member);
    const user = userEvent.setup();
    await openJobs();
    const order: string[] = [];
    for (let i = 0; i < 3 + TABS.length; i += 1) {
      await user.tab();
      const focused = document.activeElement as HTMLElement;
      order.push(focused.getAttribute("aria-label") ?? focused.textContent);
    }
    expect(order).toEqual([
      "Skip to content",
      "jobapp",
      expect.stringMatching(/^Account menu/),
      ...TABS,
    ]);
    // The skip link goes past the bar, to the page.
    expect(
      screen.getByRole("link", { name: "Skip to content" }),
    ).toHaveAttribute("href", "#main");
  });

  it("keeps the page clear of the bar, safe area included", async () => {
    phoneScreen();
    fakeSession(member);
    await openJobs();
    const frame = screen.getByRole("main").parentElement!;
    expect(frame.style.getPropertyValue("--bottom-bar")).toContain(
      "env(safe-area-inset-bottom)",
    );
    expect(frame.className).toContain("pb-(--bottom-bar)");
  });
});
