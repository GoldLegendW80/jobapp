import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LinkedInCard, type LinkedIn } from "./linkedin-card";

const TEXT =
  "LinkedIn's terms forbid automated use of its site, and your account may be limited or closed; the app keeps to the pace of a person, but the risk is yours.";

function state(over: Partial<LinkedIn> = {}): LinkedIn {
  return {
    enabled: false,
    accepted: false,
    allowed: false,
    text: TEXT,
    text_version: 1,
    accepted_at: null,
    accepted_version: null,
    query: null,
    location: null,
    paused_until: null,
    last_sweep_at: null,
    needs_signin: false,
    ...over,
  };
}

function mount(first: LinkedIn, after?: LinkedIn) {
  const calls: { method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body: unknown =
        typeof init?.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ method, body });
      return Promise.resolve(
        new Response(
          JSON.stringify(method === "PUT" ? (after ?? first) : first),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      );
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <LinkedInCard />
    </QueryClientProvider>,
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LinkedInCard", () => {
  it("is off by default and shows the sentence before turning on", async () => {
    const calls = mount(
      state(),
      state({ enabled: true, accepted: true, allowed: true }),
    );
    const toggle = await screen.findByRole("switch", {
      name: "LinkedIn search",
    });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByText(TEXT)).toBeNull();
    await userEvent.type(screen.getByLabelText("Search words"), "AI Engineer");
    await userEvent.click(toggle);
    expect(screen.getByText(TEXT)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    await userEvent.click(
      screen.getByRole("button", { name: "I accept, turn LinkedIn on" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: "LinkedIn search" }),
      ).toHaveAttribute("aria-checked", "true"),
    );
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).toMatchObject({
      enabled: true,
      accept_version: 1,
      query: "AI Engineer",
    });
  });

  it("asks again when the sentence changed", async () => {
    mount(
      state({
        enabled: true,
        accepted: false,
        query: "AI",
        accepted_version: 1,
        text_version: 2,
      }),
    );
    expect(await screen.findByText(TEXT)).toBeInTheDocument();
    expect(screen.getByText(/LinkedIn stays stopped/)).toBeInTheDocument();
  });

  it("tells the pause and the sign in wall", async () => {
    mount(
      state({
        enabled: true,
        accepted: true,
        paused_until: "2026-10-27T09:00:00Z",
      }),
    );
    expect(await screen.findByText(/paused until/)).toBeInTheDocument();
  });

  it("tells the person to sign in", async () => {
    mount(state({ enabled: true, accepted: true, needs_signin: true }));
    expect(
      await screen.findByText(/LinkedIn needs you to sign in/),
    ).toBeInTheDocument();
  });

  it("writes no dash as a separator", async () => {
    mount(
      state({
        enabled: true,
        accepted: false,
        paused_until: "2026-10-27T09:00:00Z",
        needs_signin: true,
      }),
    );
    await screen.findByText(TEXT);
    const text = document.body.textContent;
    expect(text).not.toMatch(/ - |—|–/);
  });
});
