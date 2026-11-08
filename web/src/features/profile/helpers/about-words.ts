/** The words of "About you" (about-document.tsx) on the profile page. */
import type { PartKind } from "../api/about-api";

const plural = (count: number, one: string, more = `${one}s`) =>
  `${count} ${count === 1 ? one : more}`;

export const ABOUT_WORDS = {
  title: "About you",
  intro:
    "Your skills, your answers to application forms and the facts about your work. Write a fact part in your own words and click Done: it is saved as written. With a model key your skills and answers are text too, and the app shows what it would change in your profile.",
  loading: "Loading your document",
  notLoaded: "Your document could not be loaded",
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
  refused: "Left as it was:",
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
    `${plural(count, "advanced category", "advanced categories")} (regular expressions) ${count === 1 ? "stays as it is and keeps" : "stay as they are and keep"} counting in the fit score.`,
  newPart: "New part",
  addPart: "Add a part",
  missing: (count: number) =>
    `${plural(count, "fact block")} of your profile ${count === 1 ? "is" : "are"} not in this document.`,
  addThem: "Add them",
  plainSkills: "What the fit of a posting reads, changed here by hand.",
  plainAnswers:
    "What the apply agent writes when a form asks something your CV does not say, changed here by hand.",
  plainKey: "With a model key you write them as text and the app reads it.",
  years: "Years of experience",
  yearsUnit: "years",
  yearsHelp: "Postings that ask for more years than this lose points.",
  moved: (heading: string, direction: "up" | "down") =>
    `${heading} moved ${direction}.`,
  orderNotSaved: "The order of your fact blocks was not saved.",
  size: "Fact base size",
  overSize: "Over the limit: shorten or remove a fact part before you save it.",
} as const;
