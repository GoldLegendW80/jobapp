/**
 * The notes on an offer letter for tests and stories (api/app/documents/offer_router.py): none
 * read yet, notes written on an invented letter, and a fake of GET and POST that keeps what was
 * asked. Every letter, figure and name is made up.
 */
import { http, HttpResponse } from "msw";

import type { OfferNotes } from "@/features/documents/api/offer-notes-api";

import { server } from "./server";

export const NO_OFFER_NOTES: OfferNotes = {
  document_id: null,
  language: null,
  notes: null,
  version: null,
  reading: null,
  busy: false,
  letter: null,
  pay: { posting: { pay: null }, pay_sought: [] },
  truncated: false,
  read_chars: null,
};

export const OFFER_NOTES: OfferNotes = {
  document_id: "offer-notes-1",
  language: "fr",
  notes: {
    summary:
      "Lumen Freight vous propose le poste d'ingénieur données senior en CDI, à partir du 1er décembre 2026, pour 4 500 euros bruts par mois.",
    clauses: [
      {
        title: "Période d'essai",
        quote: "La période d'essai est de quatre mois, renouvelable une fois.",
        plain:
          "Pendant les quatre premiers mois, vous ou l'employeur pouvez mettre fin au contrat rapidement. L'employeur peut la renouveler une fois ; la lettre ne dit pas pour combien de temps.",
      },
      {
        title: "Non-concurrence",
        quote:
          "Pendant douze mois après votre départ, vous ne travaillerez pas pour un concurrent en France.",
        plain:
          "Pendant un an après votre départ, vous vous engagez à ne pas travailler pour une entreprise que l'employeur considère comme concurrente, en France. La lettre ne dit pas si vous êtes payé pendant cette année.",
      },
    ],
    differences: [
      "La lettre indique 4 500 euros bruts par mois ; l'annonce indiquait de 55 000 à 65 000 EUR par an.",
    ],
    questions: [
      "Période d'essai : combien de temps peut durer le renouvellement ?",
      "Non-concurrence : une indemnité est-elle due pendant les douze mois ?",
    ],
  },
  version: 1,
  reading: { id: "reading-1", status: "done", error: null },
  busy: false,
  letter: { id: "letter-1", name: "promesse-embauche.pdf" },
  pay: {
    posting: { pay: "55,000 to 65,000 EUR a year" },
    pay_sought: ["60,000 to 70,000 EUR a year"],
  },
  truncated: false,
  read_chars: null,
};

/** GET answers `notes`; POST answers `afterRead` and is recorded. */
export function fakeOfferNotes(
  notes: OfferNotes,
  afterRead: OfferNotes = {
    ...notes,
    busy: true,
    reading: { id: "reading-2", status: "running", error: null },
  },
) {
  const state = { notes, reads: 0, posted: [] as unknown[] };
  server.use(
    http.get("*/api/documents/offer-notes/:id", () => {
      state.reads += 1;
      return HttpResponse.json(state.notes);
    }),
    http.post("*/api/documents/offer-notes/:id", async ({ request }) => {
      state.posted.push(await request.json());
      state.notes = afterRead;
      return HttpResponse.json(afterRead, { status: 202 });
    }),
  );
  return state;
}
