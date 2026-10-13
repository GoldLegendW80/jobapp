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
  it("shows a loading state first, with Retry disabled", async () => {
    healthSequence(async () => {
      await delay(50);
      return ok();
    });
    renderWithQuery(<HealthDot />);
    expect(status()).toHaveTextContent("Checking");
    expect(
      screen.getByRole("button", { name: "Check the API again" }),
    ).toBeDisabled();
    expect(await within(status()).findByText("API ok")).toBeInTheDocument();
  });

  it('shows "API ok" on a 200', async () => {
    const calls = healthSequence(ok);
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("API ok")).toBeInTheDocument();
    expect(screen.queryByText("API unavailable")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Check the API again" }),
    ).toBeEnabled();
    expect(calls.count).toBe(1);
  });

  it('shows "API unavailable" with the reason on a 503', async () => {
    healthSequence(down);
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();
    expect(status()).toHaveTextContent(
      "The API answered 503: database unavailable.",
    );
    expect(screen.queryByText("API ok")).not.toBeInTheDocument();
  });

  it('shows "API unavailable" on a network error', async () => {
    healthSequence(() => HttpResponse.error());
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();
    expect(status()).toHaveTextContent("The API could not be reached.");
  });

  it('shows "API unavailable" on a 200 that is not the expected body', async () => {
    healthSequence(() =>
      HttpResponse.text("<!doctype html><title>proxy</title>"),
    );
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();
    expect(status()).toHaveTextContent(
      "The API sent a response this page cannot read.",
    );
  });

  it('shows "API unavailable" after the timeout when the api hangs', async () => {
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
      expect(screen.getByText("API unavailable")).toBeInTheDocument();
    });
    expect(status()).toHaveTextContent("The API did not answer in time.");
  });

  it("recovers when Retry is clicked after a failure", async () => {
    const user = userEvent.setup();
    const calls = healthSequence(down, ok);
    renderWithQuery(<HealthDot />);
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Check the API again" }),
    );
    expect(await screen.findByText("API ok")).toBeInTheDocument();
    expect(screen.queryByText("API unavailable")).not.toBeInTheDocument();
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
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Check the API again" }),
    );
    expect(await within(status()).findByText("Checking")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Check the API again" }),
    ).toBeDisabled();

    release();
    expect(
      await screen.findByText("The API could not be reached."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Check the API again" }),
    ).toBeEnabled();
  });

  it("sends one request when Retry is clicked twice quickly", async () => {
    const user = userEvent.setup();
    const calls = healthSequence(down, async () => {
      await delay(100);
      return ok();
    });
    renderWithQuery(<HealthDot />);
    await screen.findByText("API unavailable");
    const retry = screen.getByRole("button", { name: "Check the API again" });
    await user.click(retry);
    await user.click(retry);
    await screen.findByText("API ok");
    expect(calls.count).toBe(2);
  });

  it("does not refetch in a loop after a failure", async () => {
    const calls = healthSequence(down);
    renderWithQuery(<HealthDot />);
    await screen.findByText("API unavailable");
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(calls.count).toBe(1);
  });

  it("does not refetch on window focus or when the network comes back", async () => {
    const calls = healthSequence(down);
    renderWithQuery(<HealthDot />);
    await screen.findByText("API unavailable");
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
    await screen.findByText("API ok");
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(calls.count).toBe(1);
  });

  it("refreshes a healthy status when Retry is clicked and the api went down", async () => {
    const user = userEvent.setup();
    healthSequence(ok, down);
    renderWithQuery(<HealthDot />);
    await screen.findByText("API ok");
    await user.click(
      screen.getByRole("button", { name: "Check the API again" }),
    );
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();
  });
});

describe("HealthDot accessibility", () => {
  it("has a polite live status and a labelled button", async () => {
    healthSequence(ok);
    renderWithQuery(<HealthDot />);
    expect(status()).toHaveAttribute("aria-live", "polite");
    expect(
      screen.getByRole("button", { name: "Check the API again" }),
    ).toBeInTheDocument();
    await within(status()).findByText("API ok");
  });

  it("hides decorative icons from assistive technology", async () => {
    healthSequence(down);
    const { container } = renderWithQuery(<HealthDot />);
    await screen.findByText("API unavailable");
    const icons = container.querySelectorAll("svg");
    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons)
      expect(icon).toHaveAttribute("aria-hidden", "true");
  });

  it("reaches Retry with the keyboard and activates it with Enter", async () => {
    const user = userEvent.setup();
    const calls = healthSequence(down, ok);
    renderWithQuery(<HealthDot />);
    await screen.findByText("API unavailable");
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Check the API again" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    await screen.findByText("API ok");
    await waitFor(() => {
      expect(calls.count).toBe(2);
    });
  });
});
