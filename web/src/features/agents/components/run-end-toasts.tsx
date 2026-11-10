import { useRunEnds } from "../api/use-run-ends";
import { showRunEnd } from "./show-run-end";

/** On every page of the signed in shell: a toast when one of the person's workflow runs ends. */
export function RunEndToasts() {
  useRunEnds(showRunEnd);
  return null;
}
