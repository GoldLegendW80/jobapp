/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ORIGINS, versionSummarySchema } from "../api/documents-api";
import { ORIGIN_LABELS } from "../helpers/labels";
import { fakeVersion } from "@/test/documents";

// The same file api/tests/documents/test_version_origins_parity.py checks against `Origin`.
const fixture = JSON.parse(
  readFileSync(
    path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../../../../../api/tests/documents/fixtures/version_origins.json",
    ),
    "utf8",
  ),
) as { origins: string[] };

describe("parity with api/app/documents/models.py Origin", () => {
  it("lists every origin of the api, and only those", () => {
    expect([...ORIGINS].sort()).toEqual([...fixture.origins].sort());
  });

  it.each(fixture.origins)(
    "reads a version of origin %j and names it",
    (origin) => {
      const version = versionSummarySchema.parse({
        ...fakeVersion(),
        origin,
      });
      expect(version.origin).toBe(origin);
      expect(ORIGIN_LABELS[version.origin]).toMatch(/\S/);
    },
  );

  it("names the hiring panel's rebuild in plain words", () => {
    expect(ORIGIN_LABELS.panel).toBe("Improved by the hiring panel");
  });
});
