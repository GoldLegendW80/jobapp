import { useAskToasts } from "../api/use-ask-toasts";
import { showAsk } from "./show-ask";

/** On every page of the signed in shell: a toast when the apply agent asks for the person. */
export function AskToasts() {
  useAskToasts(showAsk);
  return null;
}
