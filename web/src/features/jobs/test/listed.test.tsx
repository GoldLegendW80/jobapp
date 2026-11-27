import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { ListedMention } from "@/components/shared/jobs/listed-mention";
import { BulkBar } from "@/components/shared/jobs/jobs-actions/bulk-bar";
import { ListedNotice } from "@/components/shared/jobs/posting/listed-notice";
import type { JobDetail } from "@/features/jobs/api/jobs-api";
import {
  cardsRefusedSentence,
  LISTED_MENTION,
  listedReadSentence,
  listedSkipped,
} from "@/features/jobs/helpers/listed-words";
import { reportedSentences } from "@/features/jobs/helpers/reported-words";
import { describeEvent } from "@/features/jobs/helpers/text";
import { makeJob } from "@/test/jobs";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

const LIST = "https://aggregator.example.org/jobs?q=genai";

function detail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    ...makeJob(3, { list_url: LIST }),
    posting_text: "GenAI engineer Dust Paris",
    legacy: {},
    files: [],
    events: [],
    events_total: 0,
    ...overrides,
  };
}

describe("a posting seen only in a list (jobs J31)", () => {
  it("names the list and its own page, and nothing for an ordinary posting", () => {
    const { rerender } = renderWithQuery(<ListedMention job={detail()} />);
    expect(screen.getByTestId("listed-mention")).toHaveTextContent(
      `${LISTED_MENTION}. Open the list or its own page.`,
    );
    expect(screen.getByRole("link", { name: "the list" })).toHaveAttribute(
      "href",
      LIST,
    );
    expect(screen.getByRole("link", { name: "its own page" })).toHaveAttribute(
      "href",
      "https://jobs.example.org/3",
    );
    rerender(<ListedMention job={detail({ url: "javascript:alert(1)" })} />);
    expect(screen.queryByRole("link", { name: "its own page" })).toBeNull();
    rerender(<ListedMention job={detail({ list_url: null })} />);
    expect(screen.queryByTestId("listed-mention")).toBeNull();
  });

  it("says the figures of a source in sentences, a zero not said", () => {
    expect(
      reportedSentences({
        new: 5,
        outside: 1,
        listed: 3,
        listed_read: 2,
        cards_refused: { card_not_seen: 2, page_readable: 1, brand_new: 1 },
      }),
    ).toBe(
      "5 new postings kept, 1 of them outside your criteria, 3 of them seen only in a list, their own page not read. " +
        "2 postings seen only in a list before had their own page read: their text is now the page's. " +
        "4 cards of a list refused by the app's check: 2 because the assistant had not seen its link and its title together on a page; " +
        "1 because its own page could be read, so it was not kept from its card; 1 because the app's check found another problem.",
    );
    expect(reportedSentences({ new: 2, listed: 0, listed_read: 0 })).toBe(
      "2 new postings kept.",
    );
    expect(cardsRefusedSentence({ duplicate: 0 })).toBe("");
    expect(listedReadSentence(1)).toBe(
      "1 posting seen only in a list before had its own page read: its text is now the page's.",
    );
    expect(listedSkipped(1)).toBe(
      "1 posting seen only in a list is skipped: its text is needed first.",
    );
  });

  it("tells its history: found in a list, its page read, its text pasted", () => {
    expect(
      describeEvent("sweep", { discovery: { listed: { list_url: LIST } } }),
    ).toBe("Found by a search in a list, its own page not read");
    expect(describeEvent("sweep", { page_read: { list_url: LIST } })).toBe(
      "A search read its own page: its text replaced the card's of the list",
    );
    expect(describeEvent("edit", { text_pasted: { list_url: LIST } })).toBe(
      "You pasted its text: it replaced the card's of the list",
    );
  });

  it("pastes its text: the api's refusal is said, then its answer closes the dialog", async () => {
    const sent: unknown[] = [];
    let refuse = true;
    server.use(
      http.put("*/api/jobs/:id/text", async ({ request }) => {
        sent.push(await request.json());
        if (refuse)
          return HttpResponse.json(
            {
              detail: "This text is too short to be the posting's.",
              code: "text_too_short",
            },
            { status: 422 },
          );
        return HttpResponse.json(detail({ list_url: null }));
      }),
    );
    renderWithQuery(<ListedNotice job={detail()} />);
    fireEvent.click(screen.getByRole("button", { name: "Paste its text" }));
    const box = await screen.findByLabelText(/^The text of Engineer 3/);
    fireEvent.change(box, { target: { value: "GenAI engineer" } });
    fireEvent.click(screen.getByRole("button", { name: "Use this text" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This text is too short to be the posting's.",
    );
    refuse = false;
    fireEvent.click(screen.getByRole("button", { name: "Use this text" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(sent).toEqual([
      { text: "GenAI engineer" },
      { text: "GenAI engineer" },
    ]);
  });

  it("skips the selected postings seen only in a list in Apply and Like, and says how many", () => {
    const selection = {
      ids: ["a", "b", "c"],
      clear: () => undefined,
      cappedFrom: null,
      selectingAll: false,
      listed: new Set(["a", "c"]),
    };
    const { rerender } = renderWithQuery(
      <MemoryRouter>
        <BulkBar selection={selection} />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(
        "2 postings seen only in a list are skipped: their text is needed first.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled();
    rerender(
      <MemoryRouter>
        <BulkBar
          selection={{ ...selection, listed: new Set(["a", "b", "c"]) }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Like" })).toBeDisabled();
  });
});
