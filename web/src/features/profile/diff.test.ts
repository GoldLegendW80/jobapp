import { describe, expect, it } from "vitest";

import { haraldLike } from "@/test/profile";

import { EMPTY } from "./api";
import {
  diffFields,
  diffLines,
  factsText,
  flattenSection,
  sameData,
  stableJson,
  type LineOp,
} from "./diff";

const show = (ops: LineOp[]) =>
  ops.map(
    (op) =>
      `${op.kind === "added" ? "+" : op.kind === "removed" ? "-" : " "}${op.text}`,
  );

describe("diffLines", () => {
  it("is all same for equal texts", () => {
    expect(show(diffLines("a\nb", "a\nb"))).toEqual([" a", " b"]);
  });

  it("handles empty texts on either side", () => {
    expect(diffLines("", "")).toEqual([]);
    expect(show(diffLines("", "a\nb"))).toEqual(["+a", "+b"]);
    expect(show(diffLines("a\nb", ""))).toEqual(["-a", "-b"]);
  });

  it("finds one changed line in the middle, removed before added", () => {
    expect(show(diffLines("a\nb\nc", "a\nB\nc"))).toEqual([
      " a",
      "-b",
      "+B",
      " c",
    ]);
  });

  it("keeps the longest common subsequence", () => {
    expect(show(diffLines("a\nb\nc\nd", "b\nx\nd\ne"))).toEqual([
      "-a",
      " b",
      "-c",
      "+x",
      " d",
      "+e",
    ]);
  });

  it("handles insertions and deletions at the edges and repeated lines", () => {
    expect(show(diffLines("x\nx\nx", "x\nx"))).toEqual([" x", " x", "-x"]);
    expect(show(diffLines("b", "a\nb"))).toEqual(["+a", " b"]);
    expect(show(diffLines("", "\n"))).toEqual(["+", "+"]);
  });

  it("keeps unicode and long lines intact", () => {
    const long = "é".repeat(5000);
    expect(show(diffLines(`${long}\n🙂`, `${long}\n🙃`))).toEqual([
      ` ${long}`,
      "-🙂",
      "+🙃",
    ]);
  });

  it("falls back to removed then added when the texts are huge", () => {
    const a = Array.from({ length: 3000 }, (_, i) => `a${String(i)}`).join(
      "\n",
    );
    const b = Array.from({ length: 3000 }, (_, i) => `b${String(i)}`).join(
      "\n",
    );
    const ops = diffLines(a, b);
    expect(ops).toHaveLength(6000);
    expect(ops[0]?.kind).toBe("removed");
    expect(ops[5999]?.kind).toBe("added");
  });
});

describe("factsText", () => {
  it("puts each block under its title", () => {
    expect(
      factsText({
        blocks: [
          { title: "ONE", body: "first" },
          { title: "TWO", body: "a\nb" },
        ],
      }),
    ).toBe("# ONE\n\nfirst\n\n# TWO\n\na\nb");
  });

  it("is empty for no blocks or a broken value", () => {
    expect(factsText({ blocks: [] })).toBe("");
    expect(factsText(null)).toBe("");
    expect(factsText({ blocks: [{ title: 3 }] })).toBe("# \n\n");
  });
});

describe("flattenSection", () => {
  it("labels identity fields and list items", () => {
    const rows = flattenSection("identity", haraldLike().identity);
    expect(rows).toContainEqual({
      path: "full_name",
      label: "Full name",
      value: "Harald Example",
    });
    expect(rows).toContainEqual({
      path: "languages.1.level",
      label: "Language 2 › Level",
      value: "Advanced (C1)",
    });
    expect(rows).toContainEqual({
      path: "linkedin_url",
      label: "LinkedIn",
      value: "https://www.linkedin.com/in/example/",
    });
  });

  it("shows empty lists and nulls as empty values", () => {
    const rows = flattenSection("experience", EMPTY.experience);
    expect(rows).toEqual([
      { path: "employers", label: "Employers", value: "" },
      { path: "education", label: "Education", value: "" },
    ]);
  });

  it("merges a term list into one field and marks regular expressions", () => {
    const rows = flattenSection("skills", haraldLike().skills);
    expect(rows).toContainEqual({
      path: "years_experience",
      label: "Years of experience",
      value: "3.5",
    });
    expect(rows).toContainEqual({
      path: "have.1.terms",
      label: "Have 2 › Terms",
      value: "\\bllm(?:s)?\\b, \\bgenai\\b (regular expressions)",
    });
    expect(rows).toContainEqual({
      path: "gates.0.penalty",
      label: "Gate 1 › Penalty",
      value: "25",
    });
    expect(rows.some((row) => row.label.includes("Advanced regex"))).toBe(
      false,
    );
  });

  it("flattens search: terms, booleans, string lists, boards", () => {
    const rows = flattenSection("search", haraldLike().search);
    expect(rows).toContainEqual({
      path: "title_include.terms",
      label: "Title include terms",
      value: "applied ai, llm (regular expressions)",
    });
    expect(rows).toContainEqual({
      path: "location.terms",
      label: "Location",
      value: "Paris, Remote",
    });
    expect(rows).toContainEqual({
      path: "contract_types",
      label: "Contract types",
      value: "CDI, Freelance",
    });
    expect(rows).toContainEqual({
      path: "boards.1.enabled",
      label: "Board 2 › Enabled",
      value: "No",
    });
    expect(rows).toContainEqual({
      path: "boards.0.extra_exclude.terms",
      label: "Board 1 › Extra exclude terms",
      value: "",
    });
  });

  it("labels the automatic search apart from the posting age", () => {
    const rows = flattenSection("search", haraldLike().search);
    expect(rows).toContainEqual({
      path: "days",
      label: "Posting age (days)",
      value: "30",
    });
    expect(rows).toContainEqual({
      path: "sweep_schedule.days",
      label: "Automatic search › Days",
      value: "monday, tuesday, wednesday, thursday, friday, saturday, sunday",
    });
    expect(rows).toContainEqual({
      path: "sweep_schedule.enabled",
      label: "Automatic search › On",
      value: "No",
    });
  });

  it("flattens answers with sponsorship yes, no and unknown", () => {
    const answers = haraldLike().answers;
    answers.work_authorization.push({
      country: "Spain",
      answer: "EU citizen.",
      needs_sponsorship: null,
    });
    const rows = flattenSection("answers", answers);
    expect(rows).toContainEqual({
      path: "work_authorization.1.needs_sponsorship",
      label: "Work authorisation 2 › Needs sponsorship",
      value: "Yes",
    });
    expect(rows).toContainEqual({
      path: "work_authorization.2.needs_sponsorship",
      label: "Work authorisation 3 › Needs sponsorship",
      value: "",
    });
    expect(rows).toContainEqual({
      path: "leave_blank",
      label: "Leave blank",
      value: "Voluntary self identification",
    });
  });

  it("gives facts as one text field", () => {
    expect(flattenSection("facts", { blocks: [] })).toEqual([
      { path: "facts", label: "Fact base", value: "" },
    ]);
  });
});

describe("diffFields", () => {
  it("marks changed, added (only in the revision) and removed (only current)", () => {
    const past = haraldLike().experience;
    const now = structuredClone(past);
    now.employers[0]!.title = "Senior GenAI Engineer";
    now.employers.push({
      key: "new",
      organisation: "New Co",
      title: "Engineer",
      start: "2026-01",
      end: null,
      location: null,
      contract: null,
    });
    past.education.pop();
    const rows = diffFields(
      flattenSection("experience", past),
      flattenSection("experience", now),
    );
    expect(rows.find((r) => r.path === "employers.0.title")).toMatchObject({
      status: "changed",
      revision: "GenAI Engineer",
      current: "Senior GenAI Engineer",
    });
    expect(
      rows.find((r) => r.path === "employers.3.organisation"),
    ).toMatchObject({
      status: "removed",
      revision: undefined,
      current: "New Co",
    });
    // An empty field missing on the other side is not a difference.
    expect(rows.find((r) => r.path === "employers.3.end")?.status).toBe("same");
    expect(rows.find((r) => r.path === "education.1.school")).toMatchObject({
      status: "removed",
    });
    expect(rows.find((r) => r.path === "employers.0.key")?.status).toBe("same");
  });

  it("shows a year alone made a month as a change of that one field, each as stored", () => {
    const past = haraldLike().experience;
    past.employers[0]!.start = "2021";
    past.education[0]!.end = "2023";
    const now = structuredClone(past);
    now.employers[0]!.start = "2021-03";
    now.education[0]!.end = "2023-06";
    const rows = diffFields(
      flattenSection("experience", past),
      flattenSection("experience", now),
    );
    expect(rows.filter((r) => r.status !== "same")).toEqual([
      {
        path: "employers.0.start",
        label: "Employer 1 › Start",
        revision: "2021",
        current: "2021-03",
        status: "changed",
      },
      {
        path: "education.0.end",
        label: "School 1 › End",
        revision: "2023",
        current: "2023-06",
        status: "changed",
      },
    ]);
    // The same year on both sides is no difference, and "2021" is not "2021-01".
    expect(sameData(past, structuredClone(past))).toBe(true);
    const padded = structuredClone(past);
    padded.employers[0]!.start = "2021-01";
    expect(sameData(past, padded)).toBe(false);
  });

  it("marks fields that only the revision has as added", () => {
    const rows = diffFields(
      flattenSection("identity", haraldLike().identity),
      flattenSection("identity", EMPTY.identity),
    );
    expect(rows.find((r) => r.path === "languages.0.name")).toMatchObject({
      status: "added",
      revision: "French",
      current: undefined,
    });
    expect(rows.find((r) => r.path === "full_name")).toMatchObject({
      status: "changed",
      current: "",
    });
  });

  it("finds nothing between equal sections", () => {
    const rows = diffFields(
      flattenSection("search", haraldLike().search),
      flattenSection("search", haraldLike().search),
    );
    expect(rows.every((r) => r.status === "same")).toBe(true);
  });
});

describe("stableJson and sameData", () => {
  it("ignores key order and undefined values", () => {
    expect(
      sameData(
        { a: 1, b: [1, { c: 2, d: 3 }] },
        { b: [1, { d: 3, c: 2 }], a: 1 },
      ),
    ).toBe(true);
    expect(sameData({ a: 1, x: undefined }, { a: 1 })).toBe(true);
    expect(stableJson(undefined)).toBe("null");
  });

  it("tells different data apart", () => {
    expect(sameData({ a: [1, 2] }, { a: [2, 1] })).toBe(false);
    expect(sameData({ a: null }, { a: "" })).toBe(false);
    expect(sameData(haraldLike().facts, EMPTY.facts)).toBe(false);
  });
});
