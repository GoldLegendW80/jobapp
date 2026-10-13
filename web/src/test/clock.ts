import { act } from "@testing-library/react";
import { vi } from "vitest";

/**
 * Waits for an assertion under fake timers that stand still.
 *
 * A test of a delay (a timeout, a debounce) fakes the timers without `shouldAdvanceTime`: with
 * it the fake clock also runs with the real one, so on a slow machine the delay has passed
 * before the test looks "just before it". The price of a clock that only the test moves is
 * that what follows the timer no longer happens by itself: the answer of the fetch comes in a
 * later turn of the event loop, and the query client then tells React through a timer of zero.
 * `waitFor` of Testing Library cannot be used for it, since it ends on a timer of zero of its
 * own. So this looks, moves the fake clock on by `stepMs` (which also gives the event loop a
 * turn), and looks again: a number of looks, never an amount of real time.
 */
export async function waitForOnFakeClock(
  assertion: () => void,
  stepMs = 50,
  looks = 200,
): Promise<void> {
  for (let look = 1; ; look += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(stepMs);
    });
    try {
      assertion();
      return;
    } catch (error) {
      if (look === looks) throw error;
    }
  }
}
