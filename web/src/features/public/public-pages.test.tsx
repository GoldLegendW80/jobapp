import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import * as texts from "./texts";

/** The api requests a render makes, by path. */
function watchRequests() {
  const seen: string[] = [];
  server.events.on("request:start", ({ request }) => {
    seen.push(new URL(request.url).pathname);
  });
  return seen;
}

const publicNav = () =>
  screen.getByRole("navigation", { name: "Public pages" });

describe("the sentences of /privacy", () => {
  // The plan's own words (tasks/mail/todo.md, M2), typed out once more here on purpose: a
  // reworded constant fails this test, M21 then tests each constant against the code.
  it("are the plan's sentences, with the figures from the constants", () => {
    expect(texts.WHAT_IS_READ).toBe(
      "The app looks at the sender, the recipients, the subject and the short preview of each new mail to decide whether it concerns one of your job postings. It reads and stores the whole mail only when it does. When you connect Gmail, it asks Gmail to search your last 180 days for the terms listed below. That search can also catch mail that is not about a job.",
    );
    expect(texts.AFTER_DISCONNECT).toBe(
      "After you disconnect, the app keeps for 365 days a log of the mails you sent through it, without any address or text, and keeps in the history of each posting the fact that its status changed because of a mail.",
    );
    expect(texts.DISABLED_ACCOUNT).toBe(
      "If the administrator disables your account, the app stops reading your mailbox and keeps what it stored until the account is enabled again or deleted.",
    );
    expect(texts.DELETE_ACCOUNT).toBe(
      "The app has no button to delete an account. Write to the administrator at the address below: he deletes your account and everything in it, and the access is removed at Google.",
    );
    expect(texts.LIMITED_USE).toBe(
      "The use of information received from Google APIs will adhere to the Google API Services User Data Policy, including the Limited Use requirements.",
    );
    expect(texts.HOW_LONG).toContain("within 7 days");
  });

  // Browser B11 (tasks/browser/todo.md): the disk in one sentence, said plainly.
  it("say that the disk is not encrypted and who can read the browser profile", () => {
    expect(texts.BROWSER_DISK).toBe(
      "The server's disk is not encrypted: your profile is protected by a system user of its own with access for that user only (mode 0700), no other service mounts it, no backup holds it and it is deleted with your account, but the administrator of the server can read it.",
    );
    expect(texts.BROWSER_WHERE).toContain(
      "That volume is not in the server's backups",
    );
    expect(texts.BROWSER_FORGET).toContain("choose Forget all logins");
  });

  it("use no dash as a separator", () => {
    for (const [name, value] of Object.entries(texts)) {
      if (typeof value === "string" && name !== "GOOGLE_POLICY_URL")
        expect(value, name).not.toMatch(/[–—]| - /);
    }
  });
});

describe("the contact address", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("is empty when VITE_CONTACT_EMAIL is unset or blank", () => {
    vi.stubEnv("VITE_CONTACT_EMAIL", "");
    expect(texts.contactEmail()).toBe("");
    vi.stubEnv("VITE_CONTACT_EMAIL", "   ");
    expect(texts.contactEmail()).toBe("");
  });

  it("is the trimmed value of VITE_CONTACT_EMAIL", () => {
    vi.stubEnv("VITE_CONTACT_EMAIL", "  contact@example.org ");
    expect(texts.contactEmail()).toBe("contact@example.org");
  });
});

describe.each([
  ["signed out", null],
  ["signed in", admin],
])("the public pages, %s", (_label, user) => {
  it("shows /privacy with every sentence, the terms and the contact", async () => {
    fakeSession(user);
    const seen = watchRequests();
    renderApp("/privacy");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Privacy" }),
    ).toBeInTheDocument();
    for (const sentence of [
      texts.WHAT_IS_READ,
      texts.WHERE_STORED,
      texts.ASSISTANT,
      texts.HOW_LONG,
      texts.AFTER_DISCONNECT,
      texts.DISABLED_ACCOUNT,
      texts.DISCONNECT,
      texts.DELETE_ACCOUNT,
      texts.LIMITED_USE,
      texts.BROWSER_KEPT,
      texts.BROWSER_WHERE,
      texts.BROWSER_FORGET,
      texts.BROWSER_DISK,
    ])
      expect(screen.getByText(sentence)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "The app's browser" }),
    ).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Search terms" });
    for (const term of [...texts.SEARCH_KEYWORDS, ...texts.SEARCH_SYSTEMS])
      expect(list).toHaveTextContent(term);
    expect(document.title).toBe("Privacy · jobapp");
    // Static content: no session check, no api call.
    expect(seen).toEqual([]);
  });

  it("links the configured contact address in the Contact section", async () => {
    vi.stubEnv("VITE_CONTACT_EMAIL", "contact@example.org");
    fakeSession(user);
    renderApp("/privacy");
    const section = (
      await screen.findByRole("heading", { level: 2, name: "Contact" })
    ).closest("section") as HTMLElement;
    expect(
      within(section).getByRole("link", { name: "contact@example.org" }),
    ).toHaveAttribute("href", "mailto:contact@example.org");
    expect(within(section).queryByText(texts.NO_CONTACT)).toBeNull();
    vi.unstubAllEnvs();
  });

  it("says that no contact address is published when none is configured", async () => {
    vi.stubEnv("VITE_CONTACT_EMAIL", "");
    fakeSession(user);
    renderApp("/privacy");
    const section = (
      await screen.findByRole("heading", { level: 2, name: "Contact" })
    ).closest("section") as HTMLElement;
    expect(
      within(section).getByText("No contact address is published yet."),
    ).toBeInTheDocument();
    expect(within(section).queryByRole("link")).toBeNull();
    expect(document.body.textContent).not.toContain("@");
    expect(document.querySelector('a[href^="mailto:"]')).toBeNull();
    // The sentence the plan quotes stays, whatever the contact.
    expect(screen.getByText(texts.DELETE_ACCOUNT)).toBeInTheDocument();
    vi.unstubAllEnvs();
  });

  it("shows /terms and calls no api", async () => {
    fakeSession(user);
    const seen = watchRequests();
    renderApp("/terms");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Terms" }),
    ).toBeInTheDocument();
    expect(screen.getByText(texts.TERMS_PRIVATE)).toBeInTheDocument();
    expect(screen.getByText(texts.TERMS_WARRANTY)).toBeInTheDocument();
    expect(screen.getByText(texts.TERMS_RESPONSIBILITY)).toBeInTheDocument();
    expect(seen).toEqual([]);
  });

  it("links each page to the other two and to the sign in page", async () => {
    fakeSession(user);
    renderApp("/privacy");
    await screen.findByRole("heading", { level: 1, name: "Privacy" });
    const links = within(publicNav()).getAllByRole("link");
    expect(
      links.map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["Home", "/"],
      ["Privacy", "/privacy"],
      ["Terms", "/terms"],
      ["Sign in", "/login"],
    ]);
    expect(links[1]).toHaveAttribute("aria-current", "page");
  });
});

describe("the public home page", () => {
  it("is shown to a visitor with no session, linked to the other pages", async () => {
    fakeSession(null);
    const { location } = renderApp("/");
    expect(
      await screen.findByRole("heading", { level: 1, name: "jobapp" }),
    ).toBeInTheDocument();
    expect(screen.getByText(texts.HOME_INTRO)).toBeInTheDocument();
    expect(location()).toBe("/");
    const names = within(publicNav())
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(names).toEqual(["Home", "Privacy", "Terms", "Sign in"]);
  });

  it("sends a signed in visitor to /jobs", async () => {
    fakeSession(admin);
    const { location } = renderApp("/");
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(location()).toBe("/jobs");
  });

  it("still shows when the session check fails because the api is down", async () => {
    server.use(
      http.get("*/api/auth/me", () =>
        HttpResponse.json({ detail: "boom" }, { status: 503 }),
      ),
    );
    renderApp("/");
    expect(
      await screen.findByRole("heading", { level: 1, name: "jobapp" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("search engines", () => {
  it("are told not to index each public page, and the tag leaves with the page", async () => {
    fakeSession(null);
    const robots = () => document.head.querySelectorAll('meta[name="robots"]');
    for (const path of ["/privacy", "/terms", "/"]) {
      const { unmount } = renderApp(path);
      await screen.findByRole("heading", { level: 1 });
      expect(robots(), path).toHaveLength(1);
      expect(robots()[0]).toHaveAttribute("content", "noindex, nofollow");
      unmount();
      expect(robots(), path).toHaveLength(0);
    }
  });

  it("are not told so on the sign in page", async () => {
    fakeSession(null);
    renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in" });
    expect(document.head.querySelectorAll('meta[name="robots"]')).toHaveLength(
      0,
    );
  });
});
