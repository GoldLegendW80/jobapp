import { describe, expect, it } from "vitest";

import { missingSentence } from "../helpers/coverage-text";

const SEPARATOR = /[–—]| - /;

describe("missingSentence", () => {
  it.each([
    [[], null],
    [
      ["Airflow"],
      "Airflow, named by the posting, is not in this CV. Add it only if it is true of you.",
    ],
    [
      ["Kubernetes", "Airflow"],
      "Kubernetes and Airflow, named by the posting, are not in this CV. Add one only if it is true of you.",
    ],
    [
      ["A", "B", "C"],
      "A, B and C, named by the posting, are not in this CV. Add one only if it is true of you.",
    ],
    [
      ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
      "1, 2, 3, 4, 5, 6, 7, 8 and 2 more, named by the posting, are not in this CV. Add one only if it is true of you.",
    ],
  ])("names %j", (missing, sentence) => {
    expect(missingSentence(missing)).toBe(sentence);
    expect(sentence ?? "").not.toMatch(SEPARATOR);
  });
});
