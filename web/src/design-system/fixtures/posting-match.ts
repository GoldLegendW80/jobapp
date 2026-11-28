import type { JobDetail } from "@/features/jobs/api/jobs-api";

type Match = NonNullable<JobDetail["match"]>;

/** A reading of the current text: a good career match, a strong culture one. */
export const MATCH: Match = {
  career: 72,
  culture: 86,
  strengths: [
    "You have run data pipelines in production for several years.",
    "The team is small and owns its data, as you wish.",
  ],
  gaps: ["The role is on site three days a week, you prefer remote."],
  rated_at: "2026-11-27T09:30:00Z",
  current: true,
};

/** A reading made on a text the posting has since replaced. */
export const MATCH_OLDER_TEXT: Match = { ...MATCH, current: false };

const LONG_LINE =
  "Supercalifragilisticexpialidocious-" +
  "x".repeat(60) +
  " reads as one long word that must wrap on a phone, then plain words fill the line.";

/** Three strengths and three gaps at the longest allowed, 120 characters each. */
export const MATCH_LONGEST: Match = {
  ...MATCH,
  strengths: [0, 1, 2].map((i) => LONG_LINE.slice(0, 119) + String(i)),
  gaps: [0, 1, 2].map((i) => LONG_LINE.slice(10, 129) + String(i)),
};

/** A score in each band: 100, 79, 40 and 0 at the edges of Strong, Good, Partial and Weak. */
export const MATCH_BANDS: Match[] = [
  { ...MATCH, career: 100, culture: 80 },
  { ...MATCH, career: 79, culture: 60 },
  { ...MATCH, career: 59, culture: 40 },
  { ...MATCH, career: 39, culture: 0 },
];
