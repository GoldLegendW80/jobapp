/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { PASS_REASONS } from "@/features/opportunities/helpers/deck/pass-reasons";

import {
  atsLabel,
  channelLabel,
  daysText,
  fewApplications,
  FLOORS,
  rateText,
} from "../helpers/insights";

const api = (file: string) =>
  readFileSync(
    path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../../../../../api/app/opportunities",
      file,
    ),
    "utf8",
  );

describe("the words of the numbers", () => {
  it("withholds a rate the api left null", () => {
    expect(rateText(null)).toBe("Too few to say");
    expect(rateText(0)).toBe("0%");
    expect(rateText(29)).toBe("29%");
  });

  it("counts days and applications", () => {
    expect(daysText(1)).toBe("1 day");
    expect(daysText(12.5)).toBe("12.5 days");
    expect(fewApplications(1)).toBe(
      "Rates show from 20 applications. You have 1 application so far.",
    );
  });

  it("names the sites and the sources", () => {
    expect(atsLabel("greenhouse")).toBe("Greenhouse");
    expect(atsLabel("google_careers")).toBe("Google Careers");
    expect(atsLabel("email")).toBe("By e-mail");
    expect(atsLabel("unknown")).toBe("No address");
    expect(atsLabel("teamtailor")).toBe("teamtailor");
    expect(channelLabel("linkedin")).toBe("LinkedIn");
    expect(channelLabel("mail")).toBe("Job alert e-mails");
    expect(channelLabel("remoteok")).toBe("Remote OK");
    expect(channelLabel("jobs.example.org")).toBe("jobs.example.org");
  });
});

describe("the api's own lists", () => {
  it("has the floors of api/app/opportunities/insights.py", () => {
    const source = api("insights.py");
    for (const [name, value] of Object.entries(FLOORS))
      expect(source).toMatch(new RegExp(`^${name} = ${String(value)}$`, "m"));
  });

  it("has the reasons of api/app/opportunities/passes.py", () => {
    const literal = /^Reason = Literal\[([^\]]*)\]/m.exec(api("passes.py"));
    const reasons = [...(literal?.[1] ?? "").matchAll(/"(\w+)"/g)].map(
      (m) => m[1],
    );
    expect(reasons.length).toBeGreaterThan(0);
    expect(Object.keys(PASS_REASONS)).toEqual(reasons);
  });
});
