/** The parts of "About you" on the page: their anchors, the api's state of each, what Done costs. */
import { money } from "@/features/documents/helpers/labels";

import { keyOf, type About, type AboutPart, type Part } from "../api/about-api";
import { ABOUT_WORDS as W } from "./about-words";

/** The id of a part's card, which the contents list links to. */
export const partAnchor = (index: number) => `about-part-${index}`;

/** The id of a fact part's move buttons (`<id>-up`, `<id>-down`), which focus follows. */
export const moveId = (index: number) => `${partAnchor(index)}-move`;

/** The api's state of the part with this heading, when the saved document has it. */
export function shownPart(
  about: About,
  heading: string,
): AboutPart | undefined {
  const key = keyOf(heading);
  return about.parts.find((part) => part.key === key);
}

/** Per part, the anchor "Ready for" sends the person to: the first part of each kind but facts. */
export function kindAnchors(
  about: About,
  parts: readonly Part[],
): (string | undefined)[] {
  const seen = new Set<string>();
  return parts.map((part) => {
    const kind = shownPart(about, part.heading)?.kind ?? "facts";
    if (kind === "facts" || seen.has(kind)) return undefined;
    seen.add(kind);
    return `about-${kind}`;
  });
}

/** A heading no other part has: "New part", then "New part 2", "New part 3". */
export function newPartHeading(parts: readonly Part[]): string {
  const taken = new Set(parts.map((part) => keyOf(part.heading)));
  let heading: string = W.newPart;
  for (let n = 2; taken.has(keyOf(heading)); n += 1)
    heading = `${W.newPart} ${n}`;
  return heading;
}

/** What Done on this part costs, in words, when the api priced it (an API key only). */
export function doneCost(shown: AboutPart | undefined): string | null {
  const cost = shown?.estimate?.cost;
  if (!cost) return null;
  return W.cost(
    money(cost.currency, cost.low),
    money(cost.currency, cost.high),
  );
}
