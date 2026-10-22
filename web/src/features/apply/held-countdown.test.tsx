import { act, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/test/render";

import { HeldCountdown } from "./held-countdown";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** The calls that started the countdown's tick of one second. */
const ticks = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.filter((call) => call[1] === 1000).length;

describe("the hold's countdown", () => {
  it("ticks on one interval for the whole hold and clears it when the hold ends", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2001-02-03T10:00:00Z"));
    const started = vi.spyOn(window, "setInterval");
    const cleared = vi.spyOn(window, "clearInterval");
    renderWithQuery(
      <HeldCountdown
        applicationId="app-1"
        until="2001-02-03T10:00:05Z"
        offset={0}
      />,
    );
    expect(screen.getByRole("timer")).toHaveTextContent("0:05");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.getByRole("timer")).toHaveTextContent("0:02");
    expect(ticks(started)).toBe(1);
    expect(cleared).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(screen.queryByRole("timer")).toBeNull();
    screen.getByText(/The hold has ended/);
    expect(ticks(started)).toBe(1);
    expect(cleared).toHaveBeenCalledTimes(1);
  });

  it("starts no interval for a hold already over", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2001-02-03T10:00:00Z"));
    const started = vi.spyOn(window, "setInterval");
    renderWithQuery(
      <HeldCountdown
        applicationId="app-1"
        until="2001-02-03T09:59:00Z"
        offset={0}
      />,
    );
    screen.getByText(/The hold has ended/);
    expect(ticks(started)).toBe(0);
  });
});
