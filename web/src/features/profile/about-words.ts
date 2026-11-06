/** The words of "About you" (about-document.tsx) and of the one page profile (profile-page.tsx). */
import type { PartKind } from "./about-api";

const plural = (count: number, one: string, more = `${one}s`) =>
  `${count} ${count === 1 ? one : more}`;

export const ABOUT_WORDS = {
  title: "About you",
  intro:
    "Your skills, your answers to application forms and the facts about your work, in your own words. Edit a part, then click Done: the app reads your skills and answers and shows what it would change in your profile; a fact part is saved as written.",
  loading: "Loading your document",
  notLoaded: "Your document could not be loaded",
  contents: "Parts of About you",
  kinds: {
    skills: "What the fit of a posting reads.",
    answers:
      "Your answers to application forms. Start a yes or no answer with Yes or No.",
    facts: "Facts the CV and letter writers may quote.",
  } satisfies Record<PartKind, string>,
  placeholders: {
    skills:
      "Years of experience: 5\n\n### What I have\n- Data tools: Python, SQL\n\n### What I do not have\n- Java",
    answers:
      "### Notice period\nThree months.\n\n### Background checks\nYes, I consent to background checks.",
    facts: "What you built, in your own words.",
  } satisfies Record<PartKind, string>,
  empty: "Nothing here yet.",
  heading: "Heading",
  text: "Text",
  preview: "Preview",
  needsHeading: "Give this part a heading.",
  duplicate: "Another part has this heading. Rename one of them.",
  tooLong:
    "This part is too long to read at once. Split it into shorter parts.",
  saved: "Saved",
  saving: "Saving",
  waiting: "Not saved yet",
  notSaved: "Not saved.",
  conflictTitle: "Changed in another tab",
  conflict:
    "This document was saved in another tab since you opened it. Load the latest, or keep yours and save it over the other.",
  loadLatest: "Load the latest",
  keepMine: "Keep mine",
  noKey: "The app needs a model key to update your profile from this text.",
  addKey: "Add one on the Account page",
  cost: (low: string, high: string) =>
    `Done reads this part with your model key: about ${low} to ${high}, at list prices.`,
  notApplied: "Your profile does not have this text yet.",
  update: "Update my profile",
  saveFacts: "Save to my profile",
  reading: "Reading this part",
  notRead: "This part was not read",
  understood: "What the app understood",
  refused: "Not taken, because this part does not write it:",
  stale:
    "You changed this part since Done. Click Done again to see what changes.",
  notNow: "Not now",
  close: "Close",
  applied: "Applied to your profile:",
  elsewhereTitle: "Your profile changed",
  elsewhere:
    "Your profile changed since this part was written, for example in Fill my profile. Done would replace those changes. Rewrite the part from your profile first.",
  rewrite: "Rewrite from my profile",
  advanced: (count: number) =>
    `${plural(count, "advanced category", "advanced categories")} (regular expressions) stay as they are.`,
  classic: "Edit them in the classic editor",
  newPart: "New part",
  addPart: "Add a part",
  missing: (count: number) =>
    `${plural(count, "fact block")} of your profile ${count === 1 ? "is" : "are"} not in this document.`,
  addThem: "Add them",
} as const;
