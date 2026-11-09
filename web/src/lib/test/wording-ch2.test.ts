import { describe, expect, it } from "vitest";

import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import {
  AGENT_WORDS,
  SITE_PROFILE_PROBLEM_WORDS,
  SITE_PROFILE_STATE_WORDS,
  TEMPLATE_TAG_WORDS,
  agentWord,
  readableCode,
  runErrorWords,
  siteProfileProblem,
  siteProfileStateWord,
  templateTagWord,
} from "@/lib/wording";

const DASHES = /[‒–—―]|\s-\s/;

// Every agent id registered in api/app/agents today: definitions.py (echo, triage, job_chat) and
// the `definitions.register` calls of browser/finder.py, apply/form_agent.py, apply/agents.py,
// apply/model_check.py, ingestion/agents.py, the documents writers and chats, and profile/agents.py. A new agent adds
// its id here and its words to AGENT_WORDS.
const REGISTERED = [
  "echo",
  "triage",
  "job_chat",
  "browser_discovery",
  "apply_form",
  "apply_answer",
  "apply_check",
  "profile_extract",
  "document_transcribe",
  "documents_cv",
  "documents_letter",
  "documents_base",
  "documents_design",
  "documents_proposal",
  "documents_chat_cv",
  "documents_chat_letter",
  "documents_chat_base",
  "profile_titles",
];

describe("agent words (Assistant activity)", () => {
  it("names every registered agent in words, never by its id", () => {
    expect(Object.keys(AGENT_WORDS).sort()).toEqual([...REGISTERED].sort());
    for (const id of REGISTERED) {
      const word = agentWord(id);
      expect(word, id).not.toContain("_");
      expect(word, id).not.toBe(id);
      expect(word, id).not.toMatch(DASHES);
    }
    expect(agentWord("triage")).toBe("Posting check");
    expect(agentWord("job_chat")).toBe("Chat about a posting");
    expect(agentWord("browser_discovery")).toBe("Website search");
  });

  it("reads an id it does not know as words", () => {
    expect(agentWord("cv_writer")).toBe("Cv writer");
    // A stored run of an agent removed from the code (browser B22) still reads as words.
    expect(agentWord("browser_finder")).toBe("Browser finder");
    expect(agentWord("some__new_agent")).toBe("Some new agent");
    expect(readableCode("")).toBe("");
  });
});

describe("run errors as sentences", () => {
  it.each([
    ["max_turns", "The run reached its limit of turns."],
    ["error_max_turns", "The run reached its limit of turns."],
    [
      "The agent failed (error_during_execution).",
      "The assistant stopped on an error while it worked.",
    ],
    ["weird_code", "The run stopped: weird code."],
    ["The agent failed (weird_code).", "The run stopped: weird code."],
  ])("%s reads %j", (error, sentence) => {
    expect(runErrorWords(error)).toBe(sentence);
  });

  it("keeps the sentences the runner stores", () => {
    for (const sentence of [
      "The run reached its limit of 4 turns.",
      "No model credential is saved. Add one on the Account page.",
      "The model API failed (HTTP 500).",
    ]) {
      expect(runErrorWords(sentence)).toBe(sentence);
    }
  });
});

describe("completing a profile on a site", () => {
  it("says every state and known problem in a sentence", () => {
    for (const table of [
      SITE_PROFILE_STATE_WORDS,
      SITE_PROFILE_PROBLEM_WORDS,
    ]) {
      for (const [code, words] of Object.entries(table)) {
        expect(words, code).toMatch(/^[A-Z].*\.$/);
        expect(words, code).not.toContain("_");
        expect(words, code).not.toMatch(DASHES);
      }
    }
    expect(siteProfileStateWord("needs_person")).toBe("Waiting for you.");
    expect(siteProfileStateWord("new_state")).toBe("New state.");
    expect(siteProfileProblem("save_unknown")).toBe(
      "The app cannot tell whether the site saved your changes. Check them on the site.",
    );
    expect(siteProfileProblem("new_code")).toBe(
      "This needs your review: new code.",
    );
  });
});

describe("template tags", () => {
  it("says ats friendly in plain words and keeps other tags", () => {
    expect(templateTagWord("ats friendly")).toBe(
      "Easy for hiring software to read",
    );
    expect(templateTagWord("photo")).toBe("photo");
    for (const words of Object.values(TEMPLATE_TAG_WORDS))
      expect(words).not.toMatch(/\bATS\b/);
  });
});

describe("the app's one date and count format", () => {
  it("formats dates, moments and counts one way", () => {
    expect(formatDate("2026-11-03")).toBe("3 Nov 2026");
    expect(formatDate("not a date")).toBe("not a date");
    expect(formatDate(null)).toBe("");
    // Local time: the hour depends on the machine's zone, the shape does not.
    expect(formatDateTime("2026-11-03T08:00:00Z")).toMatch(
      /^\d{1,2} (Nov|Oct) 2026, \d{2}:\d{2}$/,
    );
    expect(formatDateTime("nope")).toBe("nope");
    expect(formatCount(14115)).toBe("14,115");
    expect(formatCount(3)).toBe("3");
  });
});
