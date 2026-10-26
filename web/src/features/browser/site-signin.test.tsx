import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeScreens, FREE, ownLease } from "@/test/browser";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import type { Browser } from "./browser-api";
import { EXPIRED, SIGN_IN_HINT, SiteSignIn } from "./site-signin";

const SITE = "https://members.example.test/jobs/search";
const SIGNING_IN: Browser = {
  ...FREE,
  lease: ownLease("signin"),
  holder: "you",
};

type Site = {
  host: string;
  state: "signed_in" | "expired";
  signed_in_at: string;
};

function fakeApi(
  sites: Site[],
  done: "signed_in" | "not_signed_in" = "signed_in",
) {
  const calls = { sessions: [] as unknown[], done: [] as unknown[] };
  let browser: Browser = FREE;
  server.use(
    http.get("*/api/browser", () => HttpResponse.json(browser)),
    http.get("*/api/browser/sites", () => HttpResponse.json({ sites })),
    http.post("*/api/browser/sessions", async ({ request }) => {
      calls.sessions.push(await request.json());
      browser = SIGNING_IN;
      return HttpResponse.json(
        { lease: SIGNING_IN.lease, queue: null },
        { status: 201 },
      );
    }),
    http.post("*/api/browser/sites/done", async ({ request }) => {
      calls.done.push(await request.json());
      browser = FREE;
      if (done === "signed_in") {
        sites.push({
          host: "members.example.test",
          state: "signed_in",
          signed_in_at: "2026-10-26T10:00:00Z",
        });
        return HttpResponse.json({
          host: "members.example.test",
          state: "signed_in",
          detail: null,
        });
      }
      return HttpResponse.json({
        host: "members.example.test",
        state: "not_signed_in",
        detail:
          "No login of this website was found in your browser. Sign in, then click Done.",
      });
    }),
  );
  return calls;
}

beforeEach(() => {
  fakeScreens();
});

describe("SiteSignIn", () => {
  it("opens the website in the person's own browser, then Done records the login", async () => {
    const calls = fakeApi([]);
    renderWithQuery(<SiteSignIn url={SITE} />);
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Sign in to members.example.test",
      }),
    );
    expect(await screen.findByText(SIGN_IN_HINT)).toBeInTheDocument();
    expect(calls.sessions).toEqual([{ purpose: "signin", address: SITE }]);
    await userEvent.click(await screen.findByRole("button", { name: "Done" }));
    expect(await screen.findByText("Signed in")).toBeInTheDocument();
    expect(calls.done).toEqual([{ address: SITE }]);
    expect(screen.queryByText(SIGN_IN_HINT)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Forget all logins" }),
    ).toBeInTheDocument();
  });

  it("says so when the browser kept no login of the website", async () => {
    fakeApi([], "not_signed_in");
    renderWithQuery(<SiteSignIn url={SITE} />);
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Sign in to members.example.test",
      }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Done" }));
    expect(
      await screen.findByText(/No login of this website was found/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Signed in")).not.toBeInTheDocument();
  });

  it("asks to sign in again when a search met the website's sign in page", async () => {
    fakeApi([
      {
        host: "members.example.test",
        state: "expired",
        signed_in_at: "2026-10-20T10:00:00Z",
      },
    ]);
    renderWithQuery(<SiteSignIn url={SITE} />);
    expect(await screen.findByText(EXPIRED)).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Sign in to members.example.test" }),
      ).toHaveTextContent("Sign in again");
    });
    expect(screen.queryByText("Signed in")).not.toBeInTheDocument();
  });

  it("shows nothing for an address that is not a website", () => {
    fakeApi([]);
    const { container } = renderWithQuery(<SiteSignIn url="not a website" />);
    expect(container).toBeEmptyDOMElement();
  });
});
