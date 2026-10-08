import { describeError, errorDetail, isApiError } from "@/lib/api";

export const TOO_MANY_ATTEMPTS =
  "Too many sign in attempts. Wait a few minutes, then try again.";
export const SERVER_UNREACHABLE =
  "The server could not be reached. Check your connection and try again.";

/** What to tell the person after a failed sign in. The api's own words when it gives some. */
export function loginErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.status === 429) return errorDetail(error) ?? TOO_MANY_ATTEMPTS;
    if (error.status === 401)
      return errorDetail(error) ?? "Incorrect email or password.";
    if (error.status === 422) return errorDetail(error) ?? describeError(error);
    if (error.kind === "network" || error.kind === "timeout")
      return SERVER_UNREACHABLE;
  }
  return describeError(error);
}
