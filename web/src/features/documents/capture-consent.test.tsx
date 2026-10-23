import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeCv, fakeDocuments } from "@/test/documents";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { consentKey } from "./documents-api";

const waiting = () =>
  fakeCv({
    source: "uploaded",
    design_generation: {
      id: "waiting-capture",
      status: "waiting",
      wait_reason: "consent",
      error: null,
    },
  });
const disposers: (() => void)[] = [];
afterEach(() => {
  disposers.splice(0).forEach((dispose) => {
    dispose();
  });
});

async function show(path = "/profile/cv", count = 1) {
  fakeSession(member);
  fakeProfile();
  const api = fakeDocuments({
    cvs: [
      waiting(),
      ...Array.from({ length: count - 1 }, (_, i) =>
        fakeCv({ id: `other-${String(i)}`, name: `Other ${String(i)}` }),
      ),
    ],
  });
  const posts: Request[] = [];
  const note = ({ request }: { request: Request }) => {
    if (request.method === "POST") posts.push(request.clone());
  };
  server.events.on("request:start", note);
  disposers.push(() => {
    server.events.removeListener("request:start", note);
  });
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByText("Uploaded");
  return { api, posts, user, ...app };
}

const action = () =>
  screen.getByRole("button", { name: "Confirm what is sent" });
const agree = () =>
  screen.getByRole("button", { name: "Agree and resume waiting documents" });
const tick = () =>
  screen.getByRole("checkbox", {
    name: "I have read this and I agree to send it",
  });
const postBodies = (posts: Request[]) =>
  Promise.all(
    posts.map(async (request) => [
      new URL(request.url).pathname,
      (await request.json()) as unknown,
    ]),
  );

describe("consent for an existing waiting capture", () => {
  it.each(["/profile/cv", "/profile/cv/cv-1"])(
    "reads current text and explicitly accepts its version without creating anything at %s",
    async (path) => {
      const { api, posts, user, client } = await show(path, 10);
      if (path === "/profile/cv")
        expect(screen.getByRole("button", { name: "New CV" })).toBeDisabled();
      api.consent = {
        ...api.consent,
        version: 7,
        text: "Current server consent text.",
      };
      const reads = api.requests.length;
      await user.click(action());
      const dialog = await screen.findByRole("dialog", {
        name: "What is sent to resume your documents",
      });
      expect(within(dialog).getByTestId("consent-text")).toHaveTextContent(
        api.consent.text,
      );
      expect(within(dialog).getByText("Version 7")).toBeVisible();
      expect(within(dialog).getByTestId("consent-estimates")).toHaveTextContent(
        "One posting:",
      );
      expect(within(dialog).getByTestId("consent-estimates")).toHaveTextContent(
        "A typical week",
      );
      expect(agree()).toBeDisabled();
      expect(posts).toHaveLength(0);
      const listReads = api.requests.filter(
        (one) => one.path === "/api/documents/base-cvs",
      ).length;
      const docReads = api.requests.filter(
        (one) => one.path === "/api/documents/cv-1",
      ).length;
      await user.click(tick());
      await user.click(agree());
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
      });
      expect(await postBodies(posts)).toEqual([
        ["/api/documents/consent", { version: 7 }],
      ]);
      expect(client.getQueryData(consentKey)).toMatchObject({
        accepted: true,
        version: 7,
      });
      expect(api.cvs).toHaveLength(10);
      await waitFor(() => {
        expect(
          api.requests.filter((one) => one.path === "/api/documents/base-cvs")
            .length,
        ).toBeGreaterThan(listReads);
        if (path.endsWith("cv-1"))
          expect(
            api.requests.filter((one) => one.path === "/api/documents/cv-1")
              .length,
          ).toBeGreaterThan(docReads);
      });
      expect(
        api.requests
          .slice(reads)
          .some(
            (one) =>
              one.method === "GET" && one.path === "/api/documents/consent",
          ),
      ).toBe(true);
      expect(action()).toHaveFocus();
    },
  );

  it.each(["Not now", "Escape", "Close"])(
    "cancels with %s without a POST and returns focus",
    async (method) => {
      const { user, posts } = await show();
      action().focus();
      await user.keyboard("{Enter}");
      const dialog = await screen.findByRole("dialog");
      await user.click(tick());
      if (method === "Escape") await user.keyboard("{Escape}");
      else
        await user.click(within(dialog).getByRole("button", { name: method }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
      });
      expect(posts).toHaveLength(0);
      expect(action()).toHaveFocus();
      await user.click(action());
      expect(await screen.findByRole("checkbox")).not.toBeChecked();
      expect(agree()).toBeDisabled();
    },
  );

  it("keeps a loading or failed read unavailable and retries using fixed errors", async () => {
    const { api, posts, user } = await show();
    let release: (() => void) | undefined;
    server.use(
      http.get("*/api/documents/consent", async () => {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return HttpResponse.json(
          { detail: "synthetic-private-read" },
          { status: 503 },
        );
      }),
    );
    await user.click(action());
    expect(await screen.findByText("Loading what is sent")).toBeVisible();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(agree()).toBeDisabled();
    await waitFor(() => {
      expect(release).toBeDefined();
    });
    act(() => {
      release!();
    });
    expect(
      await screen.findByText("What is sent could not be loaded. Try again."),
    ).toBeVisible();
    expect(document.body.textContent).not.toContain("synthetic-private-read");
    expect(agree()).toBeDisabled();
    server.use(
      http.get("*/api/documents/consent", () => HttpResponse.json(api.consent)),
    );
    await user.click(
      screen.getByRole("button", { name: "Retry loading what is sent" }),
    );
    expect(await screen.findByRole("checkbox")).not.toBeChecked();
    expect(posts).toHaveLength(0);
  });

  it("refetches stale 422 text and requires a new tick before a second POST", async () => {
    const { api, posts, user } = await show();
    let calls = 0;
    server.use(
      http.post("*/api/documents/consent", () => {
        calls += 1;
        api.consent = {
          ...api.consent,
          version: 2,
          text: "Replacement server text.",
        };
        return calls === 1
          ? HttpResponse.json(
              { detail: "synthetic-private-stale" },
              { status: 422 },
            )
          : HttpResponse.json({ ...api.consent, accepted: true });
      }),
    );
    await user.click(action());
    await user.click(await screen.findByRole("checkbox"));
    await user.click(agree());
    expect(await screen.findByText("Replacement server text.")).toBeVisible();
    expect(screen.getByText("Version 2")).toBeVisible();
    expect(tick()).not.toBeChecked();
    expect(agree()).toBeDisabled();
    expect(document.body.textContent).not.toContain("synthetic-private-stale");
    await user.click(agree());
    expect(posts).toHaveLength(1);
    await user.click(tick());
    await user.click(agree());
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(await postBodies(posts)).toEqual([
      ["/api/documents/consent", { version: 1 }],
      ["/api/documents/consent", { version: 2 }],
    ]);
  });

  it("shows a fixed save failure and permits an explicit retry only", async () => {
    const { api, posts, user } = await show();
    server.use(
      http.post("*/api/documents/consent", () =>
        HttpResponse.json(
          { detail: "synthetic-private-save" },
          { status: 500 },
        ),
      ),
    );
    await user.click(action());
    await user.click(await screen.findByRole("checkbox"));
    await user.click(agree());
    expect(
      await screen.findByText("Your agreement could not be saved. Try again."),
    ).toBeVisible();
    expect(document.body.textContent).not.toContain("synthetic-private-save");
    expect(posts).toHaveLength(1);
    server.use(
      http.post("*/api/documents/consent", () =>
        HttpResponse.json({ ...api.consent, accepted: true }),
      ),
    );
    await user.click(agree());
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(await postBodies(posts)).toEqual([
      ["/api/documents/consent", { version: 1 }],
      ["/api/documents/consent", { version: 1 }],
    ]);
  });

  it.each(["Not now", "Escape"])(
    "cancels a held stale-version read with %s without another POST or delayed reopen",
    async (method) => {
      const { api, posts, user } = await show();
      let stale = false;
      let release: (() => void) | undefined;
      let completed = false;
      server.use(
        http.get("*/api/documents/consent", async () => {
          if (stale) {
            await new Promise<void>((resolve) => {
              release = resolve;
            });
            completed = true;
          }
          return HttpResponse.json({ ...api.consent, version: stale ? 2 : 1 });
        }),
        http.post("*/api/documents/consent", () => {
          stale = true;
          return HttpResponse.json(
            { detail: "Current text changed." },
            { status: 422 },
          );
        }),
      );
      try {
        await user.click(action());
        await user.click(await screen.findByRole("checkbox"));
        await user.click(agree());
        await waitFor(() => {
          expect(release).toBeDefined();
        });
        expect(screen.getByText("Loading what is sent")).toBeVisible();
        if (method === "Escape") await user.keyboard("{Escape}");
        else await user.click(screen.getByRole("button", { name: "Not now" }));
        await waitFor(() => {
          expect(screen.queryByRole("dialog")).toBeNull();
        });
        expect(action()).toHaveFocus();
        act(() => {
          release!();
        });
        await waitFor(() => {
          expect(completed).toBe(true);
        });
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(await postBodies(posts)).toEqual([
          ["/api/documents/consent", { version: 1 }],
        ]);
      } finally {
        release?.();
      }
    },
  );

  it("guards synchronous repeated activation, traps focus, and keeps another CV untouched", async () => {
    const { api, posts, user, client } = await show();
    api.cvs.push({
      ...waiting(),
      id: "other",
      name: "Other waiting CV",
      is_default: false,
    });
    await client.invalidateQueries({ queryKey: ["documents", "base-cvs"] });
    await waitFor(() => {
      expect(
        screen.getAllByRole("button", { name: "Confirm what is sent" }),
      ).toHaveLength(2);
    });
    const actions = screen.getAllByRole("button", {
      name: "Confirm what is sent",
    });
    await user.click(actions[1]!);
    const dialog = await screen.findByRole("dialog");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    await user.click(tick());
    const close = within(dialog).getByRole("button", { name: "Close" });
    close.focus();
    await user.tab();
    expect(tick()).toHaveFocus();
    await user.tab({ shift: true });
    expect(close).toHaveFocus();
    let release: (() => void) | undefined;
    server.use(
      http.post("*/api/documents/consent", async () => {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return HttpResponse.json({ ...api.consent, accepted: true });
      }),
    );
    const button = agree();
    button.focus();
    act(() => {
      fireEvent.click(button);
      fireEvent.click(button);
      fireEvent.click(button);
    });
    await user.keyboard("{Enter} {Enter}");
    await waitFor(() => {
      expect(release).toBeDefined();
    });
    expect(posts).toHaveLength(1);
    act(() => {
      release!();
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(actions[1]).toHaveFocus();
    expect(api.cvs.map((cv) => cv.design_generation?.id)).toEqual([
      "waiting-capture",
      "waiting-capture",
    ]);
    expect(await postBodies(posts)).toEqual([
      ["/api/documents/consent", { version: 1 }],
    ]);
  });
});
