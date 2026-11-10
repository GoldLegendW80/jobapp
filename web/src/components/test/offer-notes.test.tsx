import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeDocument, fakeDocuments } from "@/test/ingestion";
import { fakeJobDetail, makeDetail } from "@/test/jobs";
import {
  fakeOfferNotes,
  NO_OFFER_NOTES,
  OFFER_NOTES,
} from "@/test/offer-notes";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { OfferNotes } from "@/features/documents/api/offer-notes-api";
import {
  comparedWith,
  isOfferStage,
} from "@/features/documents/helpers/offer-notes";

const JOB_ID = "00000000-0000-4000-9000-000000000007";
/** A separator dash: an em dash, an en dash, or a hyphen between spaces. */
const SEPARATOR = /[–—]| - /;

function posting(status: string, notes: OfferNotes = NO_OFFER_NOTES) {
  fakeSession(member);
  fakeJobDetail(makeDetail({ id: JOB_ID, status }));
  return fakeOfferNotes(notes);
}

const section = () => within(screen.getByRole("region", { name: "Offer" }));

describe("the Offer section of a posting", () => {
  it("is not there, and reads nothing, before the interview", async () => {
    const api = posting("applied");
    renderApp(`/jobs/${JOB_ID}`);
    await screen.findByRole("region", { name: "Documents" });
    expect(screen.queryByRole("region", { name: "Offer" })).toBeNull();
    expect(api.reads).toBe(0);
    expect(["applied", "skipped", "found"].some(isOfferStage)).toBe(false);
    expect(["interview", "rejected", "closed"].every(isOfferStage)).toBe(true);
  });

  it("offers to read a letter from the interview on, and says what it does", async () => {
    posting("interview");
    renderApp(`/jobs/${JOB_ID}`);
    const offer = await screen.findByRole("region", { name: "Offer" });
    expect(
      within(offer).getByRole("button", { name: "Read an offer" }),
    ).toBeEnabled();
    expect(offer.textContent).toContain("It gives no verdict.");
    expect(offer.textContent).not.toMatch(SEPARATOR);
  });

  it("shows each clause in the letter's language, what differs, the questions and what it compared", async () => {
    posting("closed", OFFER_NOTES);
    renderApp(`/jobs/${JOB_ID}`);
    const notes = await screen.findByTestId("offer-notes");
    const own = notes.querySelector("[lang='fr']");
    expect(own).not.toBeNull();
    for (const clause of OFFER_NOTES.notes?.clauses ?? []) {
      expect(
        within(notes).getByRole("heading", { name: clause.title }),
      ).toBeVisible();
      expect(notes.textContent).toContain(clause.quote);
      expect(notes.textContent).toContain(clause.plain);
    }
    expect(
      within(notes).getByRole("heading", { name: "Questions for a lawyer" }),
    ).toBeVisible();
    expect(notes.textContent).toContain(comparedWith(OFFER_NOTES.pay));
    expect(notes.textContent).toContain("They are not legal advice.");
    expect(
      section().getByRole("button", { name: "Read another letter" }),
    ).toBeVisible();
    expect(
      section().getByRole("button", { name: "Ask about the notes" }),
    ).toBeVisible();
  });

  it("says when the letter was longer than what was read, and only then", async () => {
    posting("interview", {
      ...OFFER_NOTES,
      truncated: true,
      read_chars: 30_000,
    });
    renderApp(`/jobs/${JOB_ID}`);
    const said = await screen.findByTestId("offer-letter-truncated");
    expect(said.textContent).toBe(
      "Only the first 30,000 characters of the letter were read.",
    );
    expect(said.textContent).not.toMatch(SEPARATOR);
  });

  it("says nothing of a length when the whole letter was read", async () => {
    posting("interview", OFFER_NOTES);
    renderApp(`/jobs/${JOB_ID}`);
    await screen.findByTestId("offer-notes");
    expect(screen.queryByTestId("offer-letter-truncated")).toBeNull();
  });

  it("sends the chosen letter to ingestion, then reads it with no consent asked", async () => {
    const api = posting("interview");
    fakeDocuments();
    const consentCalls: string[] = [];
    server.use(
      http.all("*/api/ingestion/consent", ({ request }) => {
        consentCalls.push(request.method);
        return HttpResponse.json({
          version: 2,
          text: "The letter is sent.",
          reminder: "Sent.",
          accepted: false,
          accepted_at: null,
        });
      }),
    );
    const user = userEvent.setup();
    renderApp(`/jobs/${JOB_ID}`);
    const offer = await screen.findByRole("region", { name: "Offer" });
    await user.upload(
      within(offer).getByTestId("offer-letter-file"),
      new File(["Offer of employment"], "offer.docx"),
    );
    await within(offer).findByText(
      "Reading the letter. This takes a minute or two.",
    );
    expect(api.posted).toEqual([
      { ingestion_document_id: expect.any(String) as unknown },
    ]);
    expect(consentCalls).toEqual([]);
    expect(within(offer).queryByText("The letter is sent.")).toBeNull();
    expect(within(offer).queryByRole("checkbox")).toBeNull();
  });

  it("refuses a file that is neither a PDF nor a Word document before sending it", async () => {
    const api = posting("interview");
    const uploads = fakeDocuments();
    const user = userEvent.setup({ applyAccept: false });
    renderApp(`/jobs/${JOB_ID}`);
    const offer = await screen.findByRole("region", { name: "Offer" });
    await user.upload(
      within(offer).getByTestId("offer-letter-file"),
      new File(["x"], "offer.png"),
    );
    await within(offer).findByText("Choose a PDF or a Word document (DOCX).");
    expect(uploads.uploads).toEqual([]);
    expect(api.posted).toEqual([]);
  });

  it("says why a reading stopped and reads the same letter again", async () => {
    const api = posting("interview", {
      ...OFFER_NOTES,
      notes: null,
      version: null,
      reading: { id: "reading-1", status: "failed", error: "token_limit" },
    });
    fakeDocuments([
      fakeDocument({ id: "letter-1", name: "promesse-embauche.pdf" }),
    ]);
    const user = userEvent.setup();
    renderApp(`/jobs/${JOB_ID}`);
    const offer = await screen.findByRole("region", { name: "Offer" });
    await within(offer).findByText(
      "The reading stopped at its limit. Read the letter again.",
    );
    await user.click(
      within(offer).getByRole("button", {
        name: "Read promesse-embauche.pdf again",
      }),
    );
    await waitFor(() => {
      expect(api.posted).toEqual([{ ingestion_document_id: "letter-1" }]);
    });
  });

  it("sends a question about the notes to their chat", async () => {
    posting("interview", OFFER_NOTES);
    const sent: unknown[] = [];
    server.use(
      http.get("*/api/documents/:id/messages", () =>
        HttpResponse.json({ messages: [] }),
      ),
      http.post("*/api/documents/:id/messages", async ({ request, params }) => {
        sent.push({ id: params.id, body: await request.json() });
        return HttpResponse.json({ detail: "Busy." }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    renderApp(`/jobs/${JOB_ID}`);
    await screen.findByTestId("offer-notes");
    await user.click(
      section().getByRole("button", { name: "Ask about the notes" }),
    );
    const chat = await screen.findByTestId("offer-notes-chat");
    await user.type(
      within(chat).getByRole("textbox"),
      "Explain the trial period more simply",
    );
    await user.click(within(chat).getByRole("button", { name: /send/i }));
    await within(chat).findByText("Busy.");
    expect(sent).toEqual([
      {
        id: "offer-notes-1",
        body: {
          text: "Explain the trial period more simply",
          channel: "content",
        },
      },
    ]);
  });
});
