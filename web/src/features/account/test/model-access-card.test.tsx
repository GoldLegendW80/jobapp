import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { ModelCredential } from "@/features/agents/api/agents-api";
import { CHECK_POLL_MS } from "@/features/agents/api/use-model-access";
import { REMOVED, SAVED } from "@/features/agents/helpers/model-credential";

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
const status = () => screen.getByTestId("credential-status");
/** Waits until the field says how the credential stands. */
async function stands(text: string | RegExp) {
  await waitFor(() => {
    expect(status()).toHaveTextContent(text);
  });
  return status();
}

describe("model access card", () => {
  it("starts empty with the API key chosen and both options explained", async () => {
    credentialApi();
    await openCard();
    await stands("No credential saved. Agents need one to run.");
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

  it("copies the subscription token's command in one click", async () => {
    credentialApi();
    const { user } = await openCard();
    await stands(/No credential saved/);
    await user.click(
      within(form()).getByRole("button", { name: "Copy claude setup-token" }),
    );
    expect(await navigator.clipboard.readText()).toBe("claude setup-token");
  });

  it("has no dashes as separators in its copy", async () => {
    credentialApi({ initial: saved });
    const { container } = await openCard();
    await stands(/verified/);
    // Hyphenated words and the sk-ant- prefixes are fine; dash separators are not.
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });

  it("saves an API key, then shows only its last four characters", async () => {
    const state = credentialApi();
    const { user, container } = await openCard();
    await stands(/No credential saved/);
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
    // Checked with Anthropic at once: the field turns green.
    const said = await stands("Anthropic API key ending in WXyz, verified.");
    expect(said).toHaveAttribute("data-tone", "success");
    expect(state.verifies).toBe(1);
    expect(input.closest('[data-slot="input-group"]')).toHaveClass(
      "border-success",
    );
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("type", "password");
    expect(container.innerHTML).not.toContain(API_KEY.slice(0, 30));
    expect(
      within(form()).getByRole("button", { name: "Replace credential" }),
    ).toBeInTheDocument();
  });

  it("keeps no copy of the secret in the mutation cache after a save", async () => {
    credentialApi();
    const { user, client } = await openCard();
    await stands(/No credential saved/);
    await user.type(within(form()).getByLabelText("API key"), API_KEY);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    await stands(/ending in WXyz/);
    await waitFor(() => {
      const kept = client
        .getMutationCache()
        .getAll()
        .map((mutation) => JSON.stringify(mutation.state.variables ?? null));
      expect(kept.join("\n")).not.toContain(API_KEY);
    });
  });

  it("saves a subscription token under its own kind", async () => {
    const state = credentialApi();
    const { user } = await openCard();
    await stands(/No credential saved/);
    await user.click(
      within(form()).getByRole("radio", { name: "Claude subscription token" }),
    );
    const input = within(form()).getByLabelText("Subscription token");
    expect(input).toHaveAttribute("placeholder", "sk-ant-oat01-…");
    await user.type(input, OAUTH);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    await stands(/^Claude subscription token ending in Tok3/);
    expect(state.puts[0]).toEqual({
      kind: "claude_subscription",
      secret: OAUTH,
    });
  });

  it("asks for a value before sending anything", async () => {
    const state = credentialApi();
    const { user } = await openCard();
    await stands(/No credential saved/);
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
    await stands(/No credential saved/);
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
    await stands(/No credential saved/);
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

  it("checks a saved key that never was on opening, and turns the field green", async () => {
    const state = credentialApi({ initial: saved });
    await openCard();
    const said = await stands("Anthropic API key ending in WXyz, verified.");
    expect(said).toHaveAttribute("data-tone", "success");
    expect(state.verifies).toBe(1);
    expect(state.headers[0]?.get("X-Requested-With")).toBe("jobapp");
  });

  it("does not check again a key already verified", async () => {
    const state = credentialApi({
      initial: { ...saved, verified_at: "2026-10-09T08:00:00Z" },
    });
    await openCard();
    await stands("Anthropic API key ending in WXyz, verified.");
    expect(state.verifies).toBe(0);
  });

  it.each([
    [
      "verified",
      "Claude subscription token ending in Tok3, verified.",
      "success",
    ],
    [
      "invalid",
      "Anthropic refused this credential: it is wrong, revoked or has no access. Paste a new one.",
      "error",
    ],
  ] as const)(
    "checks a subscription token with one Claude Code run, then shows it %s",
    async (state, said, tone) => {
      const sub: ModelCredential = {
        ...saved,
        kind: "claude_subscription",
        last4: "Tok3",
      };
      const checking = {
        ...sub,
        check: {
          state: "checking" as const,
          message: "Checking the token with Claude Code.",
        },
      };
      let check: ModelCredential = sub;
      const api = credentialApi({
        initial: sub,
        get: () => HttpResponse.json(check),
        verify: () => {
          // The worker answers on a later read of the credential.
          check = {
            ...sub,
            verified_at: state === "verified" ? "2026-11-07T08:02:00Z" : null,
            check: { state, message: said },
          };
          return HttpResponse.json({
            result: "checking",
            message: "Checking the token with Claude Code.",
            credential: checking,
          });
        },
      });
      await openCard();
      expect(await screen.findByText("Checking it with Claude Code")).toBe(
        status(),
      );
      await waitFor(
        () => {
          expect(status()).toHaveTextContent(said);
        },
        { timeout: CHECK_POLL_MS * 3 },
      );
      expect(status()).toHaveAttribute("data-tone", tone);
      expect(api.verifies).toBe(1);
    },
  );

  it("turns the field red with the reason when Anthropic refuses the key, checked once", async () => {
    const state = credentialApi({
      initial: saved,
      verify: () =>
        HttpResponse.json(
          {
            detail:
              "Anthropic refused this credential: it is wrong, revoked or has no access. Paste a new one.",
          },
          { status: 400 },
        ),
    });
    await openCard();
    const said = await stands(/^Anthropic refused this credential/);
    expect(said).toHaveAttribute("data-tone", "error");
    expect(within(form()).getByLabelText("API key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    // The credential is read again after a refusal, and not checked a second time.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(state.verifies).toBe(1);
  });

  it("says when Anthropic is busy or cannot be reached", async () => {
    credentialApi({
      initial: saved,
      verify: () =>
        HttpResponse.json(
          { detail: "Anthropic could not be reached. Try again later." },
          { status: 503 },
        ),
    });
    await openCard();
    const said = await stands(
      "Anthropic could not be reached. Try again later.",
    );
    expect(said).toHaveAttribute("data-tone", "error");
  });

  it("shows a spinner in the field while the check runs", async () => {
    credentialApi({
      initial: saved,
      verify: async () => {
        await delay("infinite");
        return HttpResponse.json({});
      },
    });
    await openCard();
    const said = await stands("Checking it with Anthropic");
    expect(said).toHaveAttribute("data-tone", "busy");
    expect(
      within(form())
        .getByLabelText("API key")
        .closest('[data-slot="input-group"]'),
    ).toHaveAttribute("aria-busy", "true");
  });

  it("removes the credential only after confirmation", async () => {
    const state = credentialApi({ initial: saved });
    const { user } = await openCard();
    await stands(/ending in WXyz/);
    await user.click(within(form()).getByRole("button", { name: "Remove" }));
    let dialog = await screen.findByRole("alertdialog", {
      name: "Remove the credential?",
    });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(state.deletes).toBe(0);
    expect(status()).toHaveTextContent(/ending in WXyz/);

    await user.click(within(form()).getByRole("button", { name: "Remove" }));
    dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(await screen.findByText(REMOVED)).toBeInTheDocument();
    expect(state.deletes).toBe(1);
    await stands(/No credential saved/);
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
    await stands(/ending in WXyz/);
    await user.click(within(form()).getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Database unavailable.",
    );
  });

  it("offers a retry when the credential cannot be loaded", async () => {
    let fail = true;
    credentialApi({
      initial: saved,
      get: () =>
        fail
          ? HttpResponse.json(
              { detail: "Database unavailable." },
              { status: 503 },
            )
          : HttpResponse.json(saved),
    });
    const { user } = await openCard();
    const card = screen.getByRole("region", { name: "Model access" });
    expect(
      await within(card).findByText(
        "Your credential could not be loaded. Database unavailable.",
      ),
    ).toBeVisible();
    fail = false;
    await user.click(within(card).getByRole("button", { name: "Retry" }));
    await stands(/ending in WXyz/);
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
    await stands(/No credential saved/);
    await user.type(within(form()).getByLabelText("API key"), API_KEY);
    await user.click(
      within(form()).getByRole("button", { name: "Save credential" }),
    );
    expect(await within(form()).findByRole("alert")).toHaveTextContent(
      /CREDENTIALS_KEY is not set/,
    );
  });
});
