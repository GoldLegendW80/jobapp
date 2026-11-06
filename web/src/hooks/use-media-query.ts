import { useCallback, useSyncExternalStore } from "react";

/** Below Tailwind's `sm` breakpoint (640px): a phone. The `sm:` classes switch at the same width. */
export const PHONE_QUERY = "(max-width: 639px)";

/** Whether `query` matches now, following its changes (a rotated phone, a resized window). */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => {
        media.removeEventListener("change", onChange);
      };
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** A screen narrower than `sm`: the bottom tab bar, the bottom sheets, the folded filters. */
export function useIsPhone(): boolean {
  return useMediaQuery(PHONE_QUERY);
}
