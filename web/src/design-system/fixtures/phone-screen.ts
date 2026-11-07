import { PHONE_QUERY } from "@/hooks/use-media-query";

/** A phone's width in the stories, as the visual checks use. */
export const PHONE_WIDTH = 390;

function phoneMatch(query: string): MediaQueryList {
  return {
    matches: true,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  };
}

/**
 * A story's `beforeEach` that makes its page a phone and returns the undo: `useIsPhone` answers
 * yes (matchMedia matches the phone query) and the page, overlays included, is 390 px wide.
 * Tailwind's `sm:` classes still follow the real window.
 */
export function phoneScreen(): () => void {
  const matchMedia = window.matchMedia.bind(window);
  const style = document.body.getAttribute("style");
  window.matchMedia = (query: string) =>
    query === PHONE_QUERY ? phoneMatch(query) : matchMedia(query);
  // A transform makes the body the box of its fixed children: overlays and sheets stay inside.
  Object.assign(document.body.style, {
    width: `${String(PHONE_WIDTH)}px`,
    height: "100dvh",
    margin: "0",
    overflow: "auto",
    transform: "translateZ(0)",
    boxShadow: "0 0 0 1px var(--border)",
  });
  return () => {
    window.matchMedia = matchMedia;
    if (style === null) document.body.removeAttribute("style");
    else document.body.setAttribute("style", style);
  };
}
