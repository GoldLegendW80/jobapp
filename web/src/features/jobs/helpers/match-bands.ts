/** The four words of a career or culture score, a whole number from 0 to 100: Strong 80 to 100, Good 60 to 79, Partial 40 to 59, Weak 0 to 39. */
export function matchBand(score: number): string {
  if (score >= 80) return "Strong";
  if (score >= 60) return "Good";
  if (score >= 40) return "Partial";
  return "Weak";
}
