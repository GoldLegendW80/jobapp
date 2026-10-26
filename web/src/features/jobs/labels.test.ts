import { describe, expect, it } from "vitest";

import { SOURCE_PROBLEMS, sourceLabel, sourceName } from "./labels";

describe("sourceLabel", () => {
  it("names one browser source in words", () => {
    expect(sourceLabel("linkedin:AI Engineer/Paris")).toBe(
      "LinkedIn, AI Engineer, Paris",
    );
    expect(sourceLabel("greenhouse:example")).toBe("greenhouse:example");
  });

  it("names every source of a posting found twice (browser B10)", () => {
    expect(sourceLabel("greenhouse:example, linkedin:AI Engineer")).toBe(
      "greenhouse:example; LinkedIn, AI Engineer",
    );
    expect(sourceLabel("linkedin:AI Engineer, greenhouse:example")).toBe(
      "LinkedIn, AI Engineer; greenhouse:example",
    );
  });

  it("keeps a comma inside search words", () => {
    expect(sourceLabel("linkedin:AI Engineer, Agents")).toBe(
      "LinkedIn, AI Engineer, Agents",
    );
  });
});

describe("SOURCE_PROBLEMS", () => {
  it("says each LinkedIn refusal in words, without dashes", () => {
    for (const code of [
      "linkedin_off",
      "linkedin_paused",
      "linkedin_daily",
      "linkedin_signin",
      "linkedin_checkpoint",
    ]) {
      const sentence = SOURCE_PROBLEMS[code] ?? "";
      expect(sentence).not.toBe("");
      expect(sentence).not.toMatch(/[–—]| - /);
    }
  });
});

describe("sourceName of a job website searched in the browser (jobs J15)", () => {
  it("names the four job websites and a website of the person's own by its host", () => {
    const named = (kind: string, host: string | null = null) =>
      sourceName({ kind, query: "", location: null, host });
    expect(named("linkedin")).toBe("LinkedIn");
    expect(named("welcome_to_the_jungle")).toBe("Welcome to the Jungle");
    expect(named("indeed")).toBe("Indeed");
    expect(named("freework")).toBe("Free-Work");
    expect(named("custom", "jobs.example-robotics.test")).toBe(
      "jobs.example-robotics.test",
    );
    expect(named("custom")).toBe("custom");
    // A reader with search words keeps them, as before.
    expect(sourceName({ kind: "linkedin", query: "AI Engineer" })).toBe(
      "LinkedIn, AI Engineer",
    );
  });

  it("says each outcome of a website search in words, without dashes", () => {
    for (const code of [
      "permission_unavailable",
      "permission_revoked",
      "permission_checkpoint",
      "platform_daily",
      "platform_signin",
      "finding_persistence_pending",
      "unsupported_interaction",
      "unsupported_criteria",
      "no_credential",
      "agent_failed",
      "page_budget",
    ]) {
      const sentence = SOURCE_PROBLEMS[code] ?? "";
      expect(sentence).not.toBe("");
      expect(sentence).not.toMatch(/[–—]| - /);
    }
  });
});
