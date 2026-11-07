/** The fields of "Add a posting" as typed, and the posting they send. Pure. */
import type { addJob } from "../api/jobs-api";

export interface PostingDraft {
  company: string;
  title: string;
  location: string;
  contract_type: string;
  url: string;
  posting_text: string;
}

export const EMPTY_POSTING: PostingDraft = {
  company: "",
  title: "",
  location: "",
  contract_type: "",
  url: "",
  posting_text: "",
};

/** The posting to add: trimmed, an empty link or text sent as none, and no contract type when
 * none was picked (the api reads it from the title). */
export function newPosting(draft: PostingDraft): Parameters<typeof addJob>[0] {
  return {
    company: draft.company.trim(),
    title: draft.title.trim(),
    location: draft.location.trim(),
    ...(draft.contract_type ? { contract_type: draft.contract_type } : {}),
    url: draft.url.trim() || null,
    posting_text: draft.posting_text.trim() || null,
  };
}
