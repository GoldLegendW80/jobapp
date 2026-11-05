import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_TIMEOUT_MS } from "@/lib/api";
import { waitForOnFakeClock } from "@/test/clock";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { HealthDot } from "./health-dot";

/** Install a sequence of responders for /api/health and count the calls. */
function healthSequence(
  ...responders: Array<() => Response | Promise<Response>>
) {
  const calls = { count: 0 };
  server.use(
    http.get("*/api/health", () => {
      const responder =
        responders[Math.min(calls.count, responders.length - 1)]!;
      calls.count += 1;
      return responder();
    }),
  );
  return calls;
}

const ok = () => HttpResponse.json({ status: "ok" });
const down = () =>
  HttpResponse.json(
    { status: "error", detail: "database unavailable" },
    { status: 503 },
  );

function status() {
  return screen.getByRole("status");
}

afterEach(() => {
  vi.useRealTimers();
});

describe("HealthDot", () => {
  it("checks first with nothing visible and no button", async () => {
    healthSequence(async () => {
      await delay(50);
      return ok();
    });
    renderWithQuery(<HealthDot />);
    expect(status()).toHaveTextContent("Checking the server");
    expect(status()).toHaveClass("sr-only");
    expect(screen.queryByRole("button")).toBeNull();
    expect(
      await within(status()).findByText("Server reachable"),
    ).toBeInTheDocument();
  });

  it("shows nothing visible on a 200: screen readers alone read it", async () => {
    const calls = healthSequence(ok);
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("Server reachable")).toBeInTheDocument();
    expect(status()).toHaveClass("sr-only");
    expect(screen.queryByText("Server unreachable")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(calls.count).toBe(1);
  });

  it('shows "Server unreachable" with the reason on a 503', async () => {
    healthSequence(down);
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("Server unreachable")).toBeVisible();
    expect(status()).not.toHaveClass("sr-only");
    // The reason, for screen readers and on hover, never a status code.
    expect(status()).toHaveTextContent("database unavailable.");
    expect(status()).toHaveAttribute("title", "database unavailable.");
    expect(status()).not.toHaveTextContent("503");
    expect(screen.queryByText("Server reachable")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Check the server again" }),
    ).toBeEnabled();
  });

  it('shows "Server unreachable" on a network error', async () => {
    healthSequence(() => HttpResponse.error());
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("Server unreachable")).toBeInTheDocument();
    expect(status()).toHaveTextContent(
      "The app cannot reach the server. Check your connection, then try again.",
    );
  });

  it('shows "Server unreachable" on a 200 that is not the expected body', async () => {
    healthSequence(() =>
      HttpResponse.text("<!doctype html><title>proxy</title>"),
    );
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("Server unreachable")).toBeInTheDocument();
    expect(status()).toHaveTextContent(
      "The server sent an answer this page cannot read.",
    );
  });

  it('shows "Server unreachable" after the timeout when the api hangs', async () => {
    // The clock of the timeout is the test's alone (issue #104: with shouldAdvanceTime the
    // 100 ms before the limit were real ones, and a loaded machine spent them).
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    healthSequence(async () => {
      await delay("infinite");
      return ok();
    });
    renderWithQuery(<HealthDot />);
    expect(status()).toHaveTextContent("Checking");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS - 100);
    });
    expect(status()).toHaveTextContent("Checking");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    await waitForOnFakeClock(() => {
      expect(screen.getByText("Server unreachable")).toBeInTheDocument();
    });
    expect(status()).toHaveTextContent(
      "The server is slow to answer. Try again.",
    );
  });

  it("recovers when Retry is clicked after a failure", async () => {
    const user = userEvent.setup();
    const calls = healthSequence(down, ok);
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("Server unreachable")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Check the server again" }),
    );
    expect(await screen.findByText("Server reachable")).toBeInTheDocument();
    expect(screen.queryByText("Server unreachable")).not.toBeInTheDocument();
    expect(calls.count).toBe(2);
  });

  it("shows Checking while a retry is in flight, then the new failure", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    healthSequence(down, async () => {
      await gate;
      return HttpResponse.error();
    });
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("Server unreachable")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Check the server again" }),
    );
    expect(await within(status()).findByText("Checking")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Check the server again" }),
    ).toBeDisabled();

    release();
    expect(
      await screen.findByText(
        "The app cannot reach the server. Check your connection, then try again.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Check the server again" }),
    ).toBeEnabled();
  });

  it("sends one request when Retry is clicked twice quickly", async () => {
    const user = userEvent.setup();
    const calls = healthSequence(down, async () => {
      await delay(100);
      return ok();
    });
    renderWithQuery(<HealthDot />);
    await screen.findByText("Server unreachable");
    const retry = screen.getByRole("button", {
      name: "Check the server again",
    });
    await user.click(retry);
    await user.click(retry);
    await screen.findByText("Server reachable");
    expect(calls.count).toBe(2);
  });

  it("does not refetch in a loop after a failure", async () => {
    const calls = healthSequence(down);
    renderWithQuery(<HealthDot />);
    await screen.findByText("Server unreachable");
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(calls.count).toBe(1);
  });

  it("does not refetch on window focus or when the network comes back", async () => {
    const calls = healthSequence(down);
    renderWithQuery(<HealthDot />);
    await screen.findByText("Server unreachable");
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("online"));
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(calls.count).toBe(1);
  });

  it("does not refetch in a loop after a success", async () => {
    const calls = healthSequence(ok);
    renderWithQuery(<HealthDot />);
    await screen.findByText("Server reachable");
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(calls.count).toBe(1);
  });

  it("takes its button away once a retry finds the server again", async () => {
    const user = userEvent.setup();
    healthSequence(down, ok);
    renderWithQuery(<HealthDot />);
    await screen.findByText("Server unreachable");
    await user.click(
      screen.getByRole("button", { name: "Check the server again" }),
    );
    await screen.findByText("Server reachable");
    expect(status()).toHaveClass("sr-only");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("HealthDot accessibility", () => {
  it("has a polite live status and, when down, a labelled button", async () => {
    healthSequence(down);
    renderWithQuery(<HealthDot />);
    expect(status()).toHaveAttribute("aria-live", "polite");
    await within(status()).findByText("Server unreachable");
    expect(
      screen.getByRole("button", { name: "Check the server again" }),
    ).toBeInTheDocument();
  });

  it("hides decorative icons from assistive technology", async () => {
    healthSequence(down);
    const { container } = renderWithQuery(<HealthDot />);
    await screen.findByText("Server unreachable");
    const icons = container.querySelectorAll("svg");
    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons)
      expect(icon).toHaveAttribute("aria-hidden", "true");
  });

  it("reaches Retry with the keyboard and activates it with Enter", async () => {
    const user = userEvent.setup();
    const calls = healthSequence(down, ok);
    renderWithQuery(<HealthDot />);
    await screen.findByText("Server unreachable");
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Check the server again" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    await screen.findByText("Server reachable");
    await waitFor(() => {
      expect(calls.count).toBe(2);
    });
  });
});
