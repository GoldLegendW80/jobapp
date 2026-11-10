import { act, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { waitForOnFakeClock } from "@/test/clock";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { ACTIVITY_COUNT_POLL_MS } from "../api/activity-api";
import { ActivityCount } from "../components/activity-count";

/** Answers /api/activity/count with each number in turn, the last one again, and counts the calls. */
function counts(...numbers: number[]) {
  const calls = { count: 0 };
  server.use(
    http.get("*/api/activity/count", () => {
      const under_way = numbers[Math.min(calls.count, numbers.length - 1)];
      calls.count += 1;
      return HttpResponse.json({ under_way });
    }),
  );
  return calls;
}

function setVisibility(state: DocumentVisibilityState) {
  vi.spyOn(document, "visibilityState", "get").mockReturnValue(state);
  act(() => {
    // As a browser fires it: at the document, bubbling to the window.
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  });
}

async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("ActivityCount", () => {
  it("shows the runs under way, in words for a screen reader", async () => {
    counts(2);
    renderWithQuery(<ActivityCount />);
    expect(await screen.findByText("2")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText(/, 2 under way/)).toHaveClass("sr-only");
  });

  it("shows nothing when no run is under way", async () => {
    const calls = counts(0);
    const { container } = renderWithQuery(<ActivityCount />);
    await vi.waitFor(() => {
      expect(calls.count).toBe(1);
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows 99+ above 99", async () => {
    counts(120);
    renderWithQuery(<ActivityCount />);
    expect(await screen.findByText("99+")).toBeInTheDocument();
    expect(screen.getByText(/, 120 under way/)).toBeInTheDocument();
  });

  it("reads the count again on its interval, and hides it when the runs end", async () => {
    vi.useFakeTimers({
      toFake: [
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "Date",
      ],
    });
    const calls = counts(1, 3, 0);
    const { container } = renderWithQuery(<ActivityCount />);
    await waitForOnFakeClock(() => {
      expect(screen.getByText("1")).toBeInTheDocument();
    });
    await wait(ACTIVITY_COUNT_POLL_MS - 1000);
    expect(calls.count).toBe(1);
    await wait(1000);
    await waitForOnFakeClock(() => {
      expect(screen.getByText("3")).toBeInTheDocument();
    });
    expect(calls.count).toBe(2);
    await wait(ACTIVITY_COUNT_POLL_MS);
    await waitForOnFakeClock(() => {
      expect(container).toBeEmptyDOMElement();
    });
    expect(calls.count).toBe(3);
  });

  it("stops reading while the document is hidden, and reads at once when it shows", async () => {
    vi.useFakeTimers({
      toFake: [
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "Date",
      ],
    });
    const calls = counts(1, 4);
    renderWithQuery(<ActivityCount />);
    await waitForOnFakeClock(() => {
      expect(screen.getByText("1")).toBeInTheDocument();
    });
    setVisibility("hidden");
    await wait(ACTIVITY_COUNT_POLL_MS * 4);
    expect(calls.count).toBe(1);
    expect(screen.getByText("1")).toBeInTheDocument();

    setVisibility("visible");
    await waitForOnFakeClock(() => {
      expect(screen.getByText("4")).toBeInTheDocument();
    });
    expect(calls.count).toBe(2);
  });

  it("keeps the last count read when a read fails", async () => {
    vi.useFakeTimers({
      toFake: [
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "Date",
      ],
    });
    const calls = counts(2);
    renderWithQuery(<ActivityCount />);
    await waitForOnFakeClock(() => {
      expect(screen.getByText("2")).toBeInTheDocument();
    });
    server.use(
      http.get("*/api/activity/count", () => {
        calls.count += 1;
        return HttpResponse.json({ detail: "down" }, { status: 503 });
      }),
    );
    await wait(ACTIVITY_COUNT_POLL_MS);
    await waitForOnFakeClock(() => {
      expect(calls.count).toBe(2);
    });
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
