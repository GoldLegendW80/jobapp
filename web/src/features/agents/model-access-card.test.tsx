import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { ModelCredential } from "./api";
import { REMOVED, SAVED } from "./model-access-card";

const API_KEY = `sk-ant-api03-${"Ab1_".repeat(20)}WXyz`;
const OAUTH = `sk-ant-oat01-${"Oa7_".repeat(20)}Tok3`;
const PATH = "*/api/me/model-credential";

type Reply = () => Response | Promise<Response>;

/**
 * A fake /api/me/model-credential: GET answers the stored credential or 404, PUT stores
 * {kind, last4}, DELETE removes it, POST /verify answers `verify` (verified by default). Every
 * call is recorded with its body and headers.
 */
function credentialApi(
  options: {
    initial?: ModelCredential | null;
    put?: (body: Record<string, unknown>) => Response | undefined;
    verify?: Reply;
    get?: Reply;
  } = {},
) {
  fakeSession(admin);
  const state = {
    saved: options.initial ?? null,
    puts: [] as Array<Record<string, unknown>>,
    deletes: 0,
    verifies: 0,
    headers: [] as Headers[],
  };
  server.use(
    http.get(PATH, () => {
      if (options.get) return options.get();
      return state.saved
        ? HttpResponse.json(state.saved)
        : HttpResponse.json({ detail: "none" }, { status: 404 });
    }),
    http.put(PATH, async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      state.puts.push(body);
      state.headers.push(request.headers);
      const custom = options.put?.(body);
      if (custom) return custom;
      const secret = String(body.secret).trim();
      state.saved = {
        kind: body.kind as ModelCredential["kind"],
        last4: secret.slice(-4),
        verified_at: null,
        updated_at: "2026-10-10T08:00:00Z",
      };
      return HttpResponse.json(state.saved);
    }),
    http.delete(PATH, ({ request }) => {
      state.deletes += 1;
      state.headers.push(request.headers);
      state.saved = null;
      return new HttpResponse(null, { status: 204 });
    }),
    http.post(`${PATH}/verify`, ({ request }) => {
      state.verifies += 1;
      state.headers.push(request.headers);
      if (options.verify) return options.verify();
      if (!state.saved) return HttpResponse.json({}, { status: 404 });
      state.saved = { ...state.saved, verified_at: "2026-10-10T08:05:00Z" };
      return HttpResponse.json({
        result: "verified",
        message: "Anthropic accepted this key.",
        credential: state.saved,
      });
    }),
  );
  return state;
}

const saved: ModelCredential = {
  kind: "anthropic_api_key",
  last4: "WXyz",
  verified_at: null,
  updated_at: "2026-10-10T08:00:00Z",
};

async function openCard() {
  const utils = renderApp("/account");
  await screen.findByRole("heading", { level: 2, name: "Model access" });
  return { ...utils, user: userEvent.setup() };
}

const form = () => screen.getByRole("form", { name: "Model access" });
const savedBox = () => screen.getByRole("region", { name: "Saved credential" });

describe("model access card", () => {
  it("starts empty with the API key chosen and both options explained", async () => {
    credentialApi();
    await openCard();
    expect(await screen.findByTestId("no-credential")).toHaveTextContent(
      "No credential saved.",
    );
    const apiKey = within(form()).getByRole("radio", {
      name: "Anthropic API key",
    });
    const subscription = within(form()).getByRole("radio", {
      name: "Claude subscription token",
    });
    expect(apiKey).toBeChecked();
    expect(subscription).not.toBeChecked();
    expect(subscription).toHaveAccessibleDescription(
      /personal use only.*claude setup-token/,
    );
    const console = within(form()).getByRole("link", {
      name: "Anthropic Console",
    });
    expect(console).toHaveAttribute(
      "href",
      "https://console.anthropic.com/settings/keys",
    );
    expect(console).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(within(form()).getByLabelText("API key")).toHaveAttribute(
      "type",
      "password",
    );
  });

  it("has no dashes as separators in its copy", async () => {
    credentialApi({ initial: saved });
    const { container } = await openCard();
    await screen.findByTestId("credential-last4");
    // Hyphenated words and the sk-ant- prefixes are fine; dash separators are not.
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });

  it("saves an API key, then shows only its last four characters", async () => {
    const state = credentialApi();
    const { user, container } = await openCard();
    await screen.findByTestId("no-credential");
    const input = within(form()).getByLabelText("API key");
    await user.type(input, `  ${API_KEY} `);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    expect(await screen.findByText(SAVED)).toBeInTheDocument();
    expect(state.puts).toEqual([
      { kind: "anthropic_api_key", secret: `  ${API_KEY} ` },
    ]);
    expect(state.headers[0]?.get("X-Requested-With")).toBe("jobapp");
    expect(
      within(savedBox()).getByTestId("credential-last4"),
    ).toHaveTextContent("WXyz");
    expect(
      within(savedBox()).getByTestId("credential-status"),
    ).toHaveTextContent("Not verified");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("type", "password");
    expect(container.innerHTML).not.toContain(API_KEY.slice(0, 30));
    expect(
      within(form()).getByRole("button", { name: "Replace credential" }),
    ).toBeInTheDocument();
  });

  it("saves a subscription token under its own kind", async () => {
    const state = credentialApi();
    const { user } = await openCard();
    await screen.findByTestId("no-credential");
    await user.click(
      within(form()).getByRole("radio", { name: "Claude subscription token" }),
    );
    const input = within(form()).getByLabelText("Subscription token");
    expect(input).toHaveAttribute("placeholder", "sk-ant-oat01-…");
    await user.type(input, OAUTH);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    await screen.findByTestId("credential-last4");
    expect(state.puts[0]).toEqual({
      kind: "claude_subscription",
      secret: OAUTH,
    });
    expect(
      within(savedBox()).getByText("Claude subscription token"),
    ).toBeVisible();
  });

  it("asks for a value before sending anything", async () => {
    const state = credentialApi();
    const { user } = await openCard();
    await screen.findByTestId("no-credential");
    await user.type(within(form()).getByLabelText("API key"), "   ");
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    expect(await within(form()).findByRole("alert")).toHaveTextContent(
      "Paste the token or key.",
    );
    expect(within(form()).getByLabelText("API key")).toHaveFocus();
    expect(within(form()).getByLabelText("API key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(state.puts).toEqual([]);
  });

  it("shows the server's format error under the input", async () => {
    credentialApi({
      put: () =>
        HttpResponse.json(
          {
            detail:
              "secret: This is a Claude subscription token. Choose Claude subscription token, or paste an API key, which starts with sk-ant-api",
          },
          { status: 422 },
        ),
    });
    const { user } = await openCard();
    await screen.findByTestId("no-credential");
    const input = within(form()).getByLabelText("API key");
    await user.type(input, OAUTH);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    const error = await within(form()).findByRole("alert");
    expect(error).toHaveTextContent(/^This is a Claude subscription token/);
    expect(input).toHaveFocus();
    expect(input).toHaveValue(OAUTH); // kept, so it can be moved to the other choice
    await user.type(input, "x");
    expect(within(form()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("waits out a 429 on save", async () => {
    credentialApi({
      put: () =>
        HttpResponse.json(
          { detail: "Too many credential changes. Try again in 1 minute." },
          { status: 429, headers: { "Retry-After": "42" } },
        ),
    });
    const { user } = await openCard();
    await screen.findByTestId("no-credential");
    await user.type(within(form()).getByLabelText("API key"), API_KEY);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    expect(
      await within(form()).findByRole("button", { name: /Try again in 0:4\d/ }),
    ).toBeDisabled();
    expect(within(form()).getByRole("alert")).toHaveTextContent(
      "Too many credential changes.",
    );
  });

  it("toggles the value between hidden and shown", async () => {
    credentialApi();
    const { user } = await openCard();
    const input = within(form()).getByLabelText("API key");
    const toggle = within(form()).getByRole("button", { name: "Show api key" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "password");
  });

  it("verifies a saved key and shows the date", async () => {
    const state = credentialApi({ initial: saved });
    const { user } = await openCard();
    await screen.findByTestId("credential-last4");
    await user.click(
      within(savedBox()).getByRole("button", { name: "Verify" }),
    );
    expect(await screen.findByTestId("verify-result")).toHaveTextContent(
      "Anthropic accepted this key.",
    );
    expect(
      within(savedBox()).getByTestId("credential-status"),
    ).toHaveTextContent(/^Verified on /);
    expect(
      within(savedBox()).getByTestId("credential-status").querySelector("time"),
    ).toHaveAttribute("datetime", "2026-10-10T08:05:00Z");
    expect(state.verifies).toBe(1);
    expect(state.headers[0]?.get("X-Requested-With")).toBe("jobapp");
  });

  it("explains that a subscription token is checked by the first run", async () => {
    const sub = { ...saved, kind: "claude_subscription" as const };
    credentialApi({
      initial: sub,
      verify: () =>
        HttpResponse.json({
          result: "unverified",
          message:
            "A subscription token cannot be checked from the server without running Claude Code. It is checked by the first agent run.",
          credential: sub,
        }),
    });
    const { user } = await openCard();
    await screen.findByTestId("credential-last4");
    await user.click(
      within(savedBox()).getByRole("button", { name: "Verify" }),
    );
    expect(await screen.findByTestId("verify-result")).toHaveTextContent(
      /checked by the first agent run/,
    );
    expect(
      within(savedBox()).getByTestId("credential-status"),
    ).toHaveTextContent("Not verified");
  });

  it("shows a refused key as an error and rereads the credential", async () => {
    let gets = 0;
    const verified = { ...saved, verified_at: "2026-10-09T08:00:00Z" };
    credentialApi({
      initial: verified,
      get: () => {
        gets += 1;
        return HttpResponse.json(gets === 1 ? verified : saved);
      },
      verify: () =>
        HttpResponse.json(
          {
            detail:
              "Anthropic refused this credential: it is wrong, revoked or has no access. Paste a new one.",
          },
          { status: 400 },
        ),
    });
    const { user } = await openCard();
    expect(await screen.findByTestId("credential-status")).toHaveTextContent(
      /^Verified on/,
    );
    await user.click(
      within(savedBox()).getByRole("button", { name: "Verify" }),
    );
    expect(await screen.findByTestId("verify-result")).toHaveTextContent(
      /^Anthropic refused this credential/,
    );
    await waitFor(() => {
      expect(screen.getByTestId("credential-status")).toHaveTextContent(
        "Not verified",
      );
    });
  });

  it("counts down when Anthropic is busy", async () => {
    credentialApi({
      initial: saved,
      verify: () =>
        HttpResponse.json(
          {
            detail:
              "Anthropic is busy or rate limiting this key. Try again later.",
          },
          { status: 429, headers: { "Retry-After": "30" } },
        ),
    });
    const { user } = await openCard();
    await screen.findByTestId("credential-last4");
    await user.click(
      within(savedBox()).getByRole("button", { name: "Verify" }),
    );
    expect(await screen.findByTestId("verify-result")).toHaveTextContent(
      "Try again later.",
    );
    expect(
      within(savedBox()).getByRole("button", { name: /Try again in 0:[23]\d/ }),
    ).toBeDisabled();
  });

  it("says when the server cannot reach Anthropic", async () => {
    credentialApi({
      initial: saved,
      verify: () =>
        HttpResponse.json(
          { detail: "Anthropic could not be reached. Try again later." },
          { status: 503 },
        ),
    });
    const { user } = await openCard();
    await screen.findByTestId("credential-last4");
    await user.click(
      within(savedBox()).getByRole("button", { name: "Verify" }),
    );
    expect(await screen.findByTestId("verify-result")).toHaveTextContent(
      "Anthropic could not be reached. Try again later.",
    );
  });

  it("shows the verify button busy while the check runs", async () => {
    credentialApi({
      initial: saved,
      verify: async () => {
        await delay("infinite");
        return HttpResponse.json({});
      },
    });
    const { user } = await openCard();
    await screen.findByTestId("credential-last4");
    await user.click(
      within(savedBox()).getByRole("button", { name: "Verify" }),
    );
    const busy = await within(savedBox()).findByRole("button", {
      name: "Verifying",
    });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
  });

  it("removes the credential only after confirmation", async () => {
    const state = credentialApi({ initial: saved });
    const { user } = await openCard();
    await screen.findByTestId("credential-last4");
    await user.click(
      within(savedBox()).getByRole("button", { name: "Remove" }),
    );
    let dialog = await screen.findByRole("alertdialog", {
      name: "Remove the credential?",
    });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(state.deletes).toBe(0);
    expect(screen.getByTestId("credential-last4")).toBeInTheDocument();

    await user.click(
      within(savedBox()).getByRole("button", { name: "Remove" }),
    );
    dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(await screen.findByText(REMOVED)).toBeInTheDocument();
    expect(state.deletes).toBe(1);
    expect(await screen.findByTestId("no-credential")).toBeInTheDocument();
    expect(
      within(form()).getByRole("button", { name: "Save credential" }),
    ).toBeInTheDocument();
  });

  it("keeps the dialog open with the error when removing fails", async () => {
    credentialApi({ initial: saved });
    server.use(
      http.delete(PATH, () =>
        HttpResponse.json({ detail: "Database unavailable." }, { status: 503 }),
      ),
    );
    const { user } = await openCard();
    await screen.findByTestId("credential-last4");
    await user.click(
      within(savedBox()).getByRole("button", { name: "Remove" }),
    );
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Database unavailable.",
    );
  });

  it("offers a retry when the credential cannot be loaded", async () => {
    let fail = true;
    credentialApi({
      get: () =>
        fail
          ? HttpResponse.json(
              { detail: "Database unavailable." },
              { status: 503 },
            )
          : HttpResponse.json(saved),
    });
    const { user } = await openCard();
    const card = screen
      .getByRole("heading", { level: 2, name: "Model access" })
      .closest("[data-slot=card]") as HTMLElement;
    expect(
      await within(card).findByText("Database unavailable."),
    ).toBeVisible();
    fail = false;
    await user.click(within(card).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("credential-last4")).toHaveTextContent(
      "WXyz",
    );
  });

  it("says when the server has no encryption key", async () => {
    credentialApi({
      put: () =>
        HttpResponse.json(
          {
            detail:
              "Model credentials cannot be stored on this server yet: its CREDENTIALS_KEY is not set.",
          },
          { status: 503 },
        ),
    });
    const { user } = await openCard();
    await screen.findByTestId("no-credential");
    await user.type(within(form()).getByLabelText("API key"), API_KEY);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    expect(await within(form()).findByRole("alert")).toHaveTextContent(
      /CREDENTIALS_KEY is not set/,
    );
  });
});
