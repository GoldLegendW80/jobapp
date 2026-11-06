import { vi } from "vitest";

import { PHONE_QUERY } from "@/hooks/use-media-query";

/**
 * Make `window.matchMedia` answer as a phone does (narrower than `sm`): the phone query matches,
 * every other query does not. jsdom has no layout, so without this every test is a wide screen.
 * The spy is restored after the test (vitest `restoreMocks`).
 */
export function phoneScreen() {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query === PHONE_QUERY,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}
