import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import {
  type MailAccount,
  navigation,
  OUTCOME_TEXT,
  TAKEN,
} from "./account-api";
import {
  CONSENT_LABEL,
  CONSENT_NOT_SAVED,
  CONSENT_TEXT,
  NOT_REVOKED,
} from "./gmail-card";

const ADDRESS = "e2e-alice@example.org";

const none: MailAccount = {
  available: true,
  status: "not_connected",
  email: null,
  can_read: false,
  can_send: false,
  connected_at: null,
  last_sync_at: null,
  consent: { granted: false, at: null, version: null, current_version: 1 },
};
const connected: MailAccount = {
  ...none,
  status: "ok",
  email: ADDRESS,
  can_read: true,
  can_send: true,
  connected_at: "2026-10-14T09:00:00Z",
};

/** Signed in, with this Gmail account; records the writes and their headers. */
function gmail(
  account: MailAccount,
  options: { consent?: number; revoked?: boolean } = {},
) {
  fakeSession(admin);
  const state = { account };
  const calls = {
    consent: [] as unknown[],
    disconnect: 0,
    headers: [] as Headers[],
  };
  server.use(
    http.get("*/api/mail/account", () => HttpResponse.json(state.account)),
    http.put("*/api/mail/account/consent", async ({ request }) => {
      const body = (await request.json()) as { granted: boolean };
      calls.consent.push(body);
      calls.headers.push(request.headers);
      if (options.consent)
        return HttpResponse.json(
          { detail: "Busy, try again." },
          { status: options.consent },
        );
      state.account = {
        ...state.account,
        consent: {
          granted: body.granted,
          at: body.granted ? "2026-10-14T09:01:00Z" : null,
          version: body.granted ? 1 : null,
          current_version: 1,
        },
      };
      return HttpResponse.json(state.account);
    }),
    http.delete("*/api/mail/account", ({ request }) => {
      calls.disconnect += 1;
      calls.headers.push(request.headers);
      state.account = none;
      return HttpResponse.json({
        disconnected: true,
        revoked: options.revoked ?? true,
      });
    }),
  );
  return calls;
}

async function card() {
  const title = await screen.findByRole("heading", { name: "Gmail" });
  const found = title.closest<HTMLElement>(
    "[aria-labelledby='gmail-card-title']",
  );
  if (!found) throw new Error("the Gmail card is not labelled by its title");
  return within(found);
}

afterEach(() => {
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("the Gmail card, not connected", () => {
  it("says what is read, asks for the consent with its version, and leaves for Google", async () => {
    gmail(none);
    const go = vi.spyOn(navigation, "go").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderApp("/account");
    const found = await card();
    expect(await found.findByText("Not connected")).toBeInTheDocument();
    await user.click(found.getByRole("button", { name: "Connect Gmail" }));

    const dialog = within(
      await screen.findByRole("dialog", { name: "Connect Gmail" }),
    );
    expect(dialog.getByText("What the app reads")).toBeInTheDocument();
    expect(
      dialog.getByText(/the sender, the recipients, the subject/),
    ).toBeInTheDocument();
    expect(
      dialog.getByRole("link", { name: "Read the privacy page" }),
    ).toHaveAttribute("href", "/privacy");
    const box = dialog.getByRole("checkbox", { name: CONSENT_LABEL });
    expect(box).not.toBeChecked();
    expect(box).toHaveAccessibleDescription(`${CONSENT_TEXT} (text version 1)`);
    // The keyboard is enough: Space ticks the box.
    box.focus();
    await user.keyboard(" ");
    expect(box).toBeChecked();
    await user.click(
      dialog.getByRole("button", { name: "Continue to Google" }),
    );
    expect(go).toHaveBeenCalledExactlyOnceWith("/api/mail/oauth/start");
    expect(window.sessionStorage.getItem("jobapp.gmail.consent")).toBe("1");
    expect(
      dialog.getByRole("button", { name: "Going to Google" }),
    ).toBeDisabled();
  });

  it("does not remember a consent that was not ticked", async () => {
    gmail(none);
    vi.spyOn(navigation, "go").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderApp("/account");
    const found = await card();
    await user.click(
      await found.findByRole("button", { name: "Connect Gmail" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Continue to Google" }),
    );
    expect(window.sessionStorage.getItem("jobapp.gmail.consent")).toBeNull();
  });

  it("says so when the server is not set up for Gmail", async () => {
    gmail({ ...none, available: false });
    renderApp("/account");
    const found = await card();
    expect(
      await found.findByText("Gmail cannot be connected on this server yet."),
    ).toBeInTheDocument();
    expect(found.getByRole("button", { name: "Connect Gmail" })).toBeDisabled();
  });
});

describe("the Gmail card, back from Google", () => {
  it.each([
    ["taken", `${TAKEN}.`, "alert"],
    ["denied", OUTCOME_TEXT.denied, "status"],
    ["scope", OUTCOME_TEXT.scope, "alert"],
    ["error", OUTCOME_TEXT.error, "alert"],
  ] as const)(
    "shows a plain sentence for %s and cleans the address",
    async (word, text, role) => {
      const calls = gmail(none);
      const { location } = renderApp(`/account?gmail=${word}`);
      const found = await card();
      const notice = await found.findByRole(role);
      expect(notice).toHaveTextContent(text);
      await waitFor(() => {
        expect(location()).toBe("/account");
      });
      expect(calls.consent).toEqual([]);
    },
  );

  it("shows nothing for a word it does not know, and echoes none of it", async () => {
    gmail(none);
    const { location } = renderApp(
      "/account?gmail=%3Cb%3Ehello%3C%2Fb%3E&keep=1",
    );
    const found = await card();
    await waitFor(() => {
      expect(location()).toBe("/account?keep=1");
    });
    expect(found.queryByText(/hello/)).not.toBeInTheDocument();
    expect(found.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("records the consent that was ticked before leaving, once connected", async () => {
    window.sessionStorage.setItem("jobapp.gmail.consent", "1");
    const calls = gmail(connected);
    renderApp("/account?gmail=connected");
    const found = await card();
    expect(await found.findByRole("status")).toHaveTextContent(
      OUTCOME_TEXT.connected,
    );
    await waitFor(() => {
      expect(calls.consent).toEqual([{ granted: true, version: 1 }]);
    });
    expect(calls.headers[0]?.get("X-Requested-With")).toBe("jobapp");
    expect(
      await found.findByRole("checkbox", { name: CONSENT_LABEL }),
    ).toBeChecked();
    expect(window.sessionStorage.getItem("jobapp.gmail.consent")).toBeNull();
  });

  it("says when that consent could not be saved, and records none for another outcome", async () => {
    window.sessionStorage.setItem("jobapp.gmail.consent", "1");
    const calls = gmail(connected, { consent: 409 });
    renderApp("/account?gmail=connected");
    const found = await card();
    expect(await found.findByText(CONSENT_NOT_SAVED)).toBeInTheDocument();
    expect(calls.consent).toHaveLength(1);
    expect(
      found.getByRole("checkbox", { name: CONSENT_LABEL }),
    ).not.toBeChecked();
  });
});

describe("the Gmail card, connected", () => {
  it("shows the address and the status, and saves the consent from the box", async () => {
    const calls = gmail(connected);
    const user = userEvent.setup();
    renderApp("/account");
    const found = await card();
    expect(await found.findByText(ADDRESS)).toBeInTheDocument();
    expect(found.getByText("Connected")).toBeInTheDocument();
    expect(found.getByText("not yet")).toBeInTheDocument();
    expect(found.queryByText(/Sending is off/)).not.toBeInTheDocument();
    expect(
      found.queryByRole("button", { name: "Connect Gmail" }),
    ).not.toBeInTheDocument();
    const box = found.getByRole("checkbox", { name: CONSENT_LABEL });
    await user.click(box);
    await waitFor(() => {
      expect(box).toBeChecked();
    });
    await user.click(box);
    await waitFor(() => {
      expect(box).not.toBeChecked();
    });
    expect(calls.consent).toEqual([
      { granted: true, version: 1 },
      { granted: false, version: 1 },
    ]);
  });

  it("says that sending is off, that a reconnect is needed, and that the text changed", async () => {
    gmail({
      ...connected,
      status: "needs_reconnect",
      can_send: false,
      consent: { ...connected.consent, current_version: 2 },
    });
    const go = vi.spyOn(navigation, "go").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderApp("/account");
    const found = await card();
    expect(await found.findByText("Needs reconnecting")).toBeInTheDocument();
    expect(found.getByText(/Sending is off/)).toBeInTheDocument();
    expect(found.getByRole("checkbox", { name: CONSENT_LABEL })).toBeDisabled();
    expect(
      found.getByText("This text has changed. Reload the page to read it."),
    ).toBeInTheDocument();
    await user.click(found.getByRole("button", { name: "Connect again" }));
    expect(go).toHaveBeenCalledExactlyOnceWith("/api/mail/oauth/start");
  });

  it("disconnects after a confirmation that says what is deleted and what stays", async () => {
    const calls = gmail(connected);
    const user = userEvent.setup();
    renderApp("/account");
    const found = await card();
    await user.click(await found.findByRole("button", { name: "Disconnect" }));
    const dialog = within(
      await screen.findByRole("alertdialog", { name: "Disconnect Gmail?" }),
    );
    expect(
      dialog.getByText(/Deleted from the app: the stored mails/),
    ).toBeInTheDocument();
    expect(
      dialog.getByText(/Kept: your postings and their history/),
    ).toBeInTheDocument();
    await user.click(dialog.getByRole("button", { name: "Keep it connected" }));
    expect(calls.disconnect).toBe(0);

    await user.click(found.getByRole("button", { name: "Disconnect" }));
    await user.click(
      await screen.findByRole("button", { name: "Disconnect Gmail" }),
    );
    expect(await found.findByText("Not connected")).toBeInTheDocument();
    expect(calls.disconnect).toBe(1);
    expect(calls.headers[0]?.get("X-Requested-With")).toBe("jobapp");
    expect(
      await found.findByRole("button", { name: "Connect Gmail" }),
    ).toBeEnabled();
  });

  it("tells the user to remove the access at Google when Google did not confirm", async () => {
    gmail(connected, { revoked: false });
    const user = userEvent.setup();
    renderApp("/account");
    const found = await card();
    await user.click(await found.findByRole("button", { name: "Disconnect" }));
    await user.click(
      await screen.findByRole("button", { name: "Disconnect Gmail" }),
    );
    expect(await found.findByRole("alert")).toHaveTextContent(NOT_REVOKED);
  });
});

describe("the sentences of the card", () => {
  it("use no dash as a separator", () => {
    const all = [
      CONSENT_LABEL,
      CONSENT_TEXT,
      CONSENT_NOT_SAVED,
      NOT_REVOKED,
      ...Object.values(OUTCOME_TEXT),
    ].join(" ");
    expect(all).not.toMatch(/[–—]| - /);
  });
});
