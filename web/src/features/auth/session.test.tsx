import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { request } from "@/lib/api";
import { admin, fakeSession, member, unauthorized } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

async function openMenu(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  await user.click(
    await screen.findByRole("button", { name: `Account menu, ${name}` }),
  );
  return screen.findByRole("menu");
}

describe("protected routes", () => {
  it.each([
    ["/", "/login?next=/"],
    ["/admin/users", "/login?next=/admin/users"],
    ["/jobs?status=found#top", "/login?next=/jobs%3Fstatus%3Dfound%23top"],
    ["/some/unknown/page", "/login?next=/some/unknown/page"],
  ])("%s without a session goes to %s", async (path, expected) => {
    const { location } = renderApp(path);
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toBe(expected);
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("comes back to the deep link after signing in", async () => {
    const state = fakeSession(null);
    server.use(
      http.post("*/api/auth/login", () => {
        state.user = admin;
        return HttpResponse.json(admin);
      }),
    );
    const { location } = renderApp("/admin/users");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), admin.email);
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByRole("heading", { name: "Users" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/admin/users");
  });

  it("shows nothing protected while the session is checked", async () => {
    server.use(
      http.get("*/api/auth/me", async () => {
        await delay(150);
        return HttpResponse.json(admin);
      }),
    );
    renderApp("/");
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeInTheDocument();
  });

  it("says the server cannot be reached when the session check fails, and retries", async () => {
    let call = 0;
    server.use(
      http.get("*/api/auth/me", () => {
        call += 1;
        return call === 1
          ? HttpResponse.json({ detail: "boom" }, { status: 500 })
          : HttpResponse.json(member);
      }),
    );
    const { location } = renderApp("/");
    expect(
      await screen.findByRole("heading", {
        name: "jobapp cannot reach its server",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The API answered 500: boom.",
    );
    expect(location()).toBe("/");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeInTheDocument();
  });

  it("an unknown path inside the app is a not found page", async () => {
    fakeSession(member);
    renderApp("/does/not/exist");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });
});

describe("roles", () => {
  it("shows the Admin entry to an admin, and the page behind it", async () => {
    fakeSession(admin);
    const { location } = renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const user = userEvent.setup();
    await user.click(within(nav).getByRole("link", { name: "Admin" }));
    expect(
      await screen.findByRole("heading", { name: "Users" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/admin/users");
    expect(within(nav).getByRole("link", { name: "Admin" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("hides the Admin entry from a member", async () => {
    fakeSession(member);
    renderApp("/");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(
      screen.queryByRole("link", { name: "Admin" }),
    ).not.toBeInTheDocument();
  });

  it("answers a member's deep link to /admin/users with the not found page", async () => {
    fakeSession(member);
    const { location } = renderApp("/admin/users");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Users" }),
    ).not.toBeInTheDocument();
    expect(location()).toBe("/admin/users");
  });
});

describe("user menu and sign out", () => {
  it("shows the name, the email and Sign out", async () => {
    fakeSession(member);
    renderApp("/");
    const menu = await openMenu(userEvent.setup(), member.name);
    expect(menu).toHaveTextContent(member.name);
    expect(menu).toHaveTextContent(member.email);
    expect(
      within(menu).getByRole("menuitem", { name: "Sign out" }),
    ).toBeInTheDocument();
    // Initials of a unicode name in the avatar.
    expect(screen.getByText("ZÅ")).toBeInTheDocument();
  });

  it("signs out: calls the api with the CSRF header, clears the cache, lands on /login", async () => {
    const state = fakeSession(admin);
    let csrf: string | null = null;
    server.use(
      http.post("*/api/auth/logout", ({ request }) => {
        csrf = request.headers.get("x-requested-with");
        state.user = null;
        state.logouts += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { client, location } = renderApp("/");
    await screen.findByText("API ok");
    expect(client.getQueryData(["health"])).toEqual({ status: "ok" });
    const user = userEvent.setup();
    const menu = await openMenu(user, admin.name);
    await user.click(within(menu).getByRole("menuitem", { name: "Sign out" }));
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/login");
    expect(state.logouts).toBe(1);
    expect(csrf).toBe("jobapp");
    expect(client.getQueryData(["me"])).toBeNull();
    expect(client.getQueryData(["health"])).toBeUndefined();
    expect(
      client
        .getQueryCache()
        .getAll()
        .map((q) => q.queryKey),
    ).toEqual([["me"]]);
  });

  it("the back button after sign out does not show protected content", async () => {
    fakeSession(admin);
    const { router, location } = renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Admin" }));
    await screen.findByRole("heading", { name: "Users" });
    const menu = await openMenu(user, admin.name);
    await user.click(within(menu).getByRole("menuitem", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });

    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        await router.navigate(-1);
      });
      await screen.findByRole("heading", { name: "Sign in" });
      expect(screen.queryByRole("banner")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "Jobs" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "Users" }),
      ).not.toBeInTheDocument();
      expect(location()).toMatch(/^\/login/);
    }
  });

  it("keeps the session and says so when sign out fails", async () => {
    fakeSession(admin);
    server.use(http.post("*/api/auth/logout", () => HttpResponse.error()));
    const { location } = renderApp("/");
    const user = userEvent.setup();
    const menu = await openMenu(user, admin.name);
    await user.click(within(menu).getByRole("menuitem", { name: "Sign out" }));
    expect(
      await screen.findByText(
        /Sign out failed\. The API could not be reached\./,
      ),
    ).toBeInTheDocument();
    expect(location()).toBe("/jobs");
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });
});

describe("global 401", () => {
  it("a 401 mid session sends the user to /login and keeps the page in next", async () => {
    const state = fakeSession(admin);
    const { client, location } = renderApp("/");
    // "/" redirects to /jobs in a router transition: the router's location is /jobs at once, but
    // the rendered tree (and useLocation in RequireAuth, which builds `next`) can still be "/"
    // when the health dot already says "API ok". Wait for the jobs page itself to be on screen.
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    await screen.findByText("API ok");
    // The session ends elsewhere (expired, disabled, signed out on another device).
    state.user = null;
    server.use(http.get("*/api/health", () => unauthorized()));
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Check the API again" }));
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/login?next=/jobs");
    expect(client.getQueryData(["me"])).toBeNull();
    expect(client.getQueryData(["health"])).toBeUndefined();
  });

  it("keeps a deep page, its query and hash in next", async () => {
    const state = fakeSession(admin);
    const { location } = renderApp("/admin/users?tab=invites#list");
    await screen.findByRole("heading", { name: "Users" });
    state.user = null;
    server.use(http.get("*/api/anything", () => unauthorized()));
    // Any api call answering 401 does it, whichever feature made it.
    await act(async () => {
      await request("/anything").catch(() => undefined);
    });
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/login?next=/admin/users%3Ftab%3Dinvites%23list");
  });

  it("does not leave a public page on a 401", async () => {
    fakeSession(null);
    server.use(
      http.post("*/api/auth/login", () =>
        HttpResponse.json(
          { detail: "Incorrect email or password." },
          { status: 401 },
        ),
      ),
    );
    const { location } = renderApp("/login?next=/admin/users");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "a@b.c");
    await user.type(screen.getByLabelText("Password"), "wrong one!");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("Incorrect email or password.");
    expect(location()).toBe("/login?next=/admin/users");
  });

  it("signing in again after a 401 restores the app", async () => {
    const state = fakeSession(member);
    server.use(
      http.post("*/api/auth/login", () => {
        state.user = member;
        return HttpResponse.json(member);
      }),
    );
    const { location } = renderApp("/");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    await screen.findByText("API ok");
    state.user = null;
    server.use(http.get("*/api/health", () => unauthorized()));
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Check the API again" }),
    );
    await screen.findByRole("heading", { name: "Sign in" });
    server.use(
      http.get("*/api/health", () => HttpResponse.json({ status: "ok" })),
    );
    await user.type(screen.getByLabelText("Email"), member.email);
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("API ok")).toBeInTheDocument();
    expect(location()).toBe("/jobs");
  });
});
