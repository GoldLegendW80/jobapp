import { SERVER_UNREACHABLE } from "@/features/auth/login-errors";
import { describeError, errorDetail, isApiError } from "@/lib/api";

/** What to tell the admin about a failed call: the api's own detail, or why nothing came back. */
export function message(error: unknown) {
  return isApiError(error) &&
    (error.kind === "network" || error.kind === "timeout")
    ? SERVER_UNREACHABLE
    : (errorDetail(error) ?? describeError(error));
}
