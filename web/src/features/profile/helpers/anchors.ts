/** The places of the profile page an address opens at (`/profile#contact`), and the old ones. */

export const PROFILE_ANCHORS = {
  cvs: "your-cv",
  experience: "experience",
  about: "about-you",
  matching: "advanced-matching",
  contact: "contact",
} as const;

/** The list of CVs, where a link to "the CVs" lands and where a deleted CV sends the person. */
export const CVS_PATH = `/profile#${PROFILE_ANCHORS.cvs}`;

/** Where each tab of the former tabbed editor (`/profile?tab=skills`) is on the page now. */
const TAB_ANCHORS: Record<string, string> = {
  cv: PROFILE_ANCHORS.cvs,
  identity: PROFILE_ANCHORS.contact,
  experience: PROFILE_ANCHORS.experience,
  facts: PROFILE_ANCHORS.about,
  skills: "about-skills",
  answers: "about-answers",
};

/** The hash an old `?tab=` address lands at, or "" for a tab that never existed. */
export function anchorOfTab(tab: string): string {
  const anchor = TAB_ANCHORS[tab];
  return anchor ? `#${anchor}` : "";
}

/** A part of About you that the document does not have lands at About you itself. */
export function fallbackAnchor(id: string): string | undefined {
  return id.startsWith("about-") && id !== PROFILE_ANCHORS.about
    ? PROFILE_ANCHORS.about
    : undefined;
}
