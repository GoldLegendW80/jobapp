/** The four words of a career or culture score: 80 to 100, 60 to 79, 40 to 59, 0 to 39. */
export function matchBand(score: number): string {
  if (score >= 80) return "Strong";
  if (score >= 60) return "Good";
  if (score >= 40) return "Partial";
  return "Weak";
}
