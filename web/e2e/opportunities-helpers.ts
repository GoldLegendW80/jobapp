import { expect, type APIRequestContext } from "@playwright/test";

import { stub } from "./apply-helpers";
import { CSRF, addPosting, type Person } from "./documents-helpers";

/** The fake forms server as the stack reaches it (tests/ats-forms-stub.compose.yml). */
export const STACK_FORMS = "http://atsforms:8080";

/**
 * A posting of `person` with this title, on the fake form of `ats` (or none), and the fit set by
 * hand, so the deck shows it above any threshold. Its id. One address is one posting for a
 * person (duplicates are refused), so two postings of one person on one `ats` need a `variant`.
 */
export async function deckPosting(
  request: APIRequestContext,
  person: Person,
  {
    title,
    ats,
    fit,
    variant,
  }: { title: string; ats: string | null; fit: number; variant?: string },
): Promise<string> {
  const job = await addPosting(request, {
    ...person,
    posting: { ...person.posting, title },
  });
  const url = ats
    ? (await stub.posting(ats, variant)).replace(
        process.env.E2E_ATS_FORMS_URL ?? "",
        STACK_FORMS,
      )
    : undefined;
  const read = await request.get(`/api/jobs/${job}`);
  const { updated_at } = (await read.json()) as { updated_at: string };
  const patched = await request.patch(`/api/jobs/${job}`, {
    headers: CSRF,
    data: { ...(url ? { url } : {}), fit, updated_at },
  });
  expect(patched.status()).toBe(200);
  return job;
}

export type DeckAnswer = {
  cards: Array<{ id: string; title: string }>;
  left: number;
};

export async function readDeck(
  request: APIRequestContext,
): Promise<DeckAnswer> {
  const response = await request.get("/api/postings/deck");
  expect(response.status()).toBe(200);
  return (await response.json()) as DeckAnswer;
}
