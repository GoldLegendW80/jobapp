/**
 * The tabs of the profile page, each its own address (owner, 2026-11-07: "I don't want too much
 * scroll, you can put tab"), and where the addresses of the former one page and of the former
 * tabbed editor land now.
 */

export const PROFILE_TABS = [
  { key: "cvs", path: "/profile", label: "CVs" },
  { key: "experience", path: "/profile/experience", label: "Experience" },
  { key: "about", path: "/profile/about", label: "About you" },
  { key: "contact", path: "/profile/contact", label: "Contact" },
] as const;
export type ProfileTab = (typeof PROFILE_TABS)[number]["key"];

/** The tab of an address, with or without a trailing slash; the CVs for any other. */
export function tabOf(pathname: string): ProfileTab {
  const path = pathname.replace(/\/+$/, "");
  return PROFILE_TABS.find((tab) => tab.path === path)?.key ?? "cvs";
}

/** The ids inside the tabs that links point at (`/profile#your-cv`, `#about-skills`). */
export const PROFILE_ANCHORS = {
  cvs: "your-cv",
  experience: "experience",
  about: "about-you",
  contact: "contact",
} as const;

/** The list of CVs, where a link to "the CVs" lands and where a deleted CV sends the person. */
export const CVS_PATH = `/profile#${PROFILE_ANCHORS.cvs}`;

/** Where the former Advanced matching tab lands: the skills of About you. */
export const SKILLS_PATH = "/profile/about#about-skills";

/** Where each tab of the former tabbed editor (`/profile?tab=skills`) is now. */
const FORMER_TABS: Record<string, string> = {
  cv: "/profile",
  identity: "/profile/contact",
  experience: "/profile/experience",
  facts: "/profile/about",
  skills: "/profile/about#about-skills",
  answers: "/profile/about#about-answers",
};

/** The address an old `?tab=` address lands at; the CVs for a tab that never existed. */
export function pathOfFormerTab(tab: string): string {
  return FORMER_TABS[tab] ?? "/profile";
}

/**
 * The tab that holds a place of the former one page (`/profile#contact`), or undefined when it
 * is on the CVs tab (`#your-cv`) or nowhere.
 */
export function pathOfAnchor(id: string): string | undefined {
  if (id === PROFILE_ANCHORS.experience) return "/profile/experience";
  if (id === PROFILE_ANCHORS.contact) return "/profile/contact";
  if (id === "advanced-matching") return SKILLS_PATH;
  if (id === PROFILE_ANCHORS.about) return "/profile/about";
  if (id.startsWith("about-")) return `/profile/about#${id}`;
  return undefined;
}

/** A part of About you that the document does not have lands at About you itself. */
export function fallbackAnchor(id: string): string | undefined {
  return id.startsWith("about-") && id !== PROFILE_ANCHORS.about
    ? PROFILE_ANCHORS.about
    : undefined;
}
