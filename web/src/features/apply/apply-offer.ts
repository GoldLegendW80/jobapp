/**
 * When Apply is offered, and why it waits: one place for the posting page's Apply section and the
 * Jobs list's rows (owner, 2026-11-05: "Apply also appears on Jobs list rows"), so both offer it
 * under the same conditions and give the same reasons. No dashes as separators.
 */
import type { ApplySettings } from "./settings-api";

/** What Apply does, by the person's "Let me review each filled form before it is sent" box. */
export const APPLY_SENDS =
  "The assistant fills the form and sends it. You can watch it live here.";
export const APPLY_REVIEWS =
  "The assistant fills the form, then you check it and send it.";

export const LINKEDIN_OFF =
  "To use Easy Apply, add LinkedIn to your list in Find jobs and save: that gives LinkedIn's permission. You can also apply yourself.";

/**
 * A posting on LinkedIn starts nothing until the person has turned LinkedIn on and accepted its
 * current sentence (SPEC-browser decision 2); `posting` is the job page to open instead, when the
 * address is one.
 */
export function linkedInOf(url: string | null): {
  onLinkedIn: boolean;
  posting: string | null;
} {
  try {
    const address = new URL(url ?? "");
    const host = address.hostname.toLowerCase().replace(/\.$/, "");
    const onLinkedIn =
      host === "linkedin.com" || host.endsWith(".linkedin.com");
    const posting =
      address.protocol === "https:" &&
      !address.username &&
      !address.password &&
      ["linkedin.com", "www.linkedin.com"].includes(host) &&
      /^\/jobs\/view\/\d{1,24}\/?$/.test(address.pathname) &&
      !address.hash
        ? address.href
        : null;
    return { onLinkedIn, posting };
  } catch {
    // A posting without an address already has its normal refusal.
    return { onLinkedIn: false, posting: null };
  }
}

/** Why Apply cannot be clicked yet, or null: what a click does is the person's setting. */
export function applyWaits(settings: {
  data: ApplySettings | undefined;
  isError: boolean;
}): string | null {
  if (settings.data) return null;
  return settings.isError
    ? "Your apply settings could not be read, so Apply waits."
    : "Reading your apply settings";
}

/** What a click on Apply does, once the settings are read. */
export const applyDoes = (settings: ApplySettings) =>
  settings.review_before_send ? APPLY_REVIEWS : APPLY_SENDS;

/** The same, said on a row of the Jobs list, where nothing is watched live. */
export const applyDoesFromList = (settings: ApplySettings) =>
  `${
    settings.review_before_send
      ? APPLY_REVIEWS
      : "The assistant fills the form and sends it."
  } Without a CV and letter, they are written first.`;
