import { describe, expect, it } from "vitest";

import {
  PROTECTED,
  permissionPlan,
  platformOf,
  readerWords,
  tickedOf,
  withdraws,
  type Permission,
  type PermissionInput,
  type Protected,
} from "./site-permissions";

const own = (url: string) => ({ platform: "custom" as const, url });

// The server's reading of an address (api/tests/opportunities/test_b14_policy.py and
// platform_policy.platform_of): any host label but the last that names a platform.
describe("platformOf", () => {
  it.each([
    ["https://www.linkedin.com/jobs/", "linkedin"],
    ["https://fr.linkedin.com/", "linkedin"],
    ["https://linkedin.com.example.test/", "linkedin"],
    ["https://lnkd.in/abc", "linkedin"],
    ["https://WWW.LINKEDIN.COM/", "linkedin"],
    ["https://www.linkedin.com./", "linkedin"],
    ["https://fr.indeed.com/emplois", "indeed"],
    ["https://indeed.fr/", "indeed"],
    ["https://www.indeed.co.uk/", "indeed"],
    ["https://jobs.indeed.example.org/", "indeed"],
    ["https://www.free-work.com/fr/tech-it/jobs", "freework"],
    ["https://freework.example.org/", "freework"],
    ["https://www.welcometothejungle.com/fr/jobs", "welcome_to_the_jungle"],
    ["https://wttj.co/", "welcome_to_the_jungle"],
    ["https://www.indeed.com:8443/", "indeed"],
    ["http://indeed.com/", "indeed"],
  ])("reads %s as %s", (url, platform) => {
    expect(platformOf(own(url))).toBe(platform);
  });

  it.each([
    "https://careers.example.org/",
    "https://example.org/indeed",
    "https://example.org/?site=linkedin.com",
    "https://notindeed.com/",
    "https://indeedjobs.example.org/",
    "https://linked-in.example.org/",
    "https://free.work.example.org/",
    "https://welcome-to-the-jungle.example.org/",
    "https://jungle.example.org/",
    "https://example.linkedin/", // the last label names no platform
    "https://example.indeed/",
    "https://xn--indee-hya.com/", // a lookalike, punycode on both sides
    "https://indeed@example.org/",
    "not an address",
    "",
  ])("reads %s as no job website", (url) => {
    expect(platformOf(own(url))).toBeNull();
  });

  it("takes a named website's platform as it is saved", () => {
    expect(
      platformOf({ platform: "indeed", url: "https://www.indeed.com/" }),
    ).toBe("indeed");
  });

  it("counts ticked websites only, once per platform", () => {
    const site = (url: string, enabled = true) => ({
      platform: "custom" as const,
      url,
      enabled,
      mode: "llm_browser" as const,
    });
    expect([
      ...tickedOf([
        site("https://fr.indeed.com/"),
        site("https://www.indeed.com/"),
        site("https://www.linkedin.com/", false),
        site("https://careers.example.org/"),
      ]),
    ]).toEqual(["indeed"]);
  });
});

function state(changes: Partial<Permission> = {}): Permission {
  return {
    enabled: false,
    accepted: false,
    allowed: false,
    text: "The current risk sentence.",
    text_version: 3,
    query: "robot engineer",
    paused_until: null,
    ...changes,
  };
}

function input(changes: Partial<PermissionInput> = {}): PermissionInput {
  const states = Object.fromEntries(PROTECTED.map((kind) => [kind, state()]));
  const shown = Object.fromEntries(PROTECTED.map((kind) => [kind, 3]));
  return {
    ticked: new Set(),
    opened: new Set(),
    websitesOnly: new Set(),
    states,
    shown,
    words: "Robot Engineer",
    ...changes,
  };
}

describe("permissionPlan", () => {
  it("writes nothing when no box is ticked and no permission is on", () => {
    expect(permissionPlan(input())).toEqual({ changes: [] });
  });

  it.each(PROTECTED)(
    "gives %s a permission at the version shown when its box is ticked",
    (kind) => {
      const plan = permissionPlan(input({ ticked: new Set([kind]) }));
      const reader = kind === "linkedin" || kind === "freework";
      expect(plan).toEqual({
        changes: [
          {
            platform: kind,
            body: {
              enabled: true,
              accept_version: 3,
              ...(reader ? { interactive_only: true } : {}),
            },
          },
        ],
      });
    },
  );

  it.each(PROTECTED)(
    "writes nothing for a ticked %s whose permission is current, paused or not",
    (kind) => {
      for (const paused_until of [null, "2026-11-03T08:00:00Z"]) {
        const states = {
          ...input().states,
          [kind]: state({ enabled: true, accepted: true, paused_until }),
        };
        expect(
          permissionPlan(input({ ticked: new Set([kind]), states })),
        ).toEqual({ changes: [] });
      }
    },
  );

  it("accepts again a reader permission left on at an older sentence, keeping its row", () => {
    const states = {
      ...input().states,
      linkedin: state({ enabled: true, accepted: false }),
    };
    expect(
      permissionPlan(input({ ticked: new Set(["linkedin"]), states })),
    ).toEqual({
      changes: [
        { platform: "linkedin", body: { enabled: true, accept_version: 3 } },
      ],
    });
  });

  it.each([
    ["never shown", undefined],
    ["an older version shown", 2],
    ["a newer version shown", 4],
  ])("refuses a tick whose sentence was %s", (_name, shown) => {
    expect(
      permissionPlan(
        input({
          ticked: new Set(["indeed"]),
          shown: { ...input().shown, indeed: shown },
        }),
      ),
    ).toEqual({ refused: "indeed", reason: "unread" });
  });

  it("refuses a tick whose state could not be read", () => {
    const states: Partial<Record<Protected, Permission>> = {
      ...input().states,
    };
    delete states.welcome_to_the_jungle;
    expect(
      permissionPlan(
        input({ ticked: new Set(["welcome_to_the_jungle"]), states }),
      ),
    ).toEqual({ refused: "welcome_to_the_jungle", reason: "unloaded" });
  });

  it.each(["linkedin", "freework"] as const)(
    "gives a new %s reader row the words, or refuses without any",
    (kind) => {
      const states = { ...input().states, [kind]: state({ query: null }) };
      expect(
        permissionPlan(input({ ticked: new Set([kind]), states })),
      ).toEqual({
        changes: [
          {
            platform: kind,
            body: {
              enabled: true,
              accept_version: 3,
              interactive_only: true,
              query: "Robot Engineer",
            },
          },
        ],
      });
      expect(
        permissionPlan(input({ ticked: new Set([kind]), states, words: null })),
      ).toEqual({ refused: kind, reason: "words" });
    },
  );

  it("takes back every permission whose box was unticked, in a fixed order", () => {
    const on = state({ enabled: true, accepted: true });
    const states = Object.fromEntries(PROTECTED.map((kind) => [kind, on]));
    expect(
      permissionPlan(input({ opened: new Set(PROTECTED), states })),
    ).toEqual({
      changes: PROTECTED.map((platform) => ({
        platform,
        body: { enabled: false },
      })),
    });
  });

  it("takes every permission back before it gives any, each in the fixed order", () => {
    const on = state({ enabled: true, accepted: true });
    const states = {
      ...input().states,
      welcome_to_the_jungle: on,
      freework: on,
    };
    const plan = permissionPlan(
      input({
        ticked: new Set(["linkedin", "indeed"]),
        opened: new Set(["welcome_to_the_jungle", "freework"]),
        states,
      }),
    );
    expect(plan).toEqual({
      changes: [
        { platform: "welcome_to_the_jungle", body: { enabled: false } },
        { platform: "freework", body: { enabled: false } },
        {
          platform: "linkedin",
          body: { enabled: true, accept_version: 3, interactive_only: true },
        },
        { platform: "indeed", body: { enabled: true, accept_version: 3 } },
      ],
    });
  });
});

describe("withdraws", () => {
  const on = state({ enabled: true, accepted: true });
  const base = (kind: Protected, changes: Partial<PermissionInput> = {}) =>
    withdraws(kind, {
      ticked: new Set(),
      opened: new Set(),
      websitesOnly: new Set(),
      states: { [kind]: on },
      ...changes,
    });

  it.each([
    ["indeed", {}, true],
    ["welcome_to_the_jungle", {}, true],
    ["linkedin", {}, false], // given for its reader and Easy Apply, never ticked here
    ["freework", {}, false],
    ["linkedin", { opened: new Set<Protected>(["linkedin"]) }, true],
    ["freework", { websitesOnly: new Set<Protected>(["freework"]) }, true],
    ["indeed", { ticked: new Set<Protected>(["indeed"]) }, false],
    ["indeed", { states: { indeed: state() } }, false], // nothing to take back
    ["indeed", { states: {} }, false], // unknown, never ticked here
    ["indeed", { states: {}, opened: new Set<Protected>(["indeed"]) }, true],
  ] as const)("%s with %o: %s", (kind, changes, expected) => {
    expect(base(kind, changes)).toBe(expected);
  });
});

describe("readerWords", () => {
  it.each([
    [["Robot Engineer"], "Robot Engineer"],
    [["  Robot \t  Engineer  "], "Robot Engineer"],
    [["", "   ", "Roboticist"], "Roboticist"],
    [["é".repeat(250)], "é".repeat(200)],
    [[], null],
    [["   "], null],
  ])("%j gives %j", (titles, words) => {
    expect(readerWords(titles)).toBe(words);
  });
});
