/** What one line of the list of addresses says under its name, and what it offers. Pure. */
import type {
  InteractiveSite,
  LinkedInSearch,
} from "@/features/profile/api/profile-api";

import { platformOf } from "./site-permissions";
import { PLATFORM_NAMES, type SourceRow } from "./sources";

export interface LineFacts {
  /** LinkedIn, whose search the person chooses: its public job search or her browser. */
  isLinkedIn: boolean;
  /** How the app reads the address, in a few plain words. */
  how: string;
  /** The first address of its known job website: the line that says what saving does. */
  first: boolean;
  /** An address of her own at a known job website: it needs that website's permission. */
  owned: string | null;
  /** Whether the line offers to sign in to the website. */
  signIn: boolean;
}

export function lineFacts(
  row: SourceRow,
  sites: readonly InteractiveSite[],
  linkedinMode: LinkedInSearch,
): LineFacts {
  const isLinkedIn = row.kind === "website" && row.platform === "linkedin";
  const how = isLinkedIn
    ? linkedinMode === "api"
      ? "Job website, read through LinkedIn's public job search"
      : "Job website, searched by the assistant in your browser"
    : row.how;
  // The first website of a known job website says what saving does to its permission; any other
  // address of that website says it needs the same permission.
  const site = row.kind === "website" ? sites[row.index] : undefined;
  const first =
    row.platform !== null &&
    sites.findIndex((item) => platformOf(item) === row.platform) === row.index;
  const owned =
    site?.platform === "custom" && row.platform
      ? `This address is ${PLATFORM_NAMES[row.platform]}'s: its search needs ${PLATFORM_NAMES[row.platform]}'s permission.`
      : null;
  // Signing in is for a website that shows more to members; LinkedIn read through its public
  // job search ("Without signing in") is never opened in the browser, so it offers none.
  const signIn =
    row.kind === "website" && !(isLinkedIn && linkedinMode === "api");
  return { isLinkedIn, how, first, owned, signIn };
}
