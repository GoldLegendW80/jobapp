import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

import { BulkApplyDialog } from "@/components/shared/apply/bulk-apply";
import { PostingNotices } from "@/components/shared/jobs/posting/posting-notices";
import { NOTICES } from "@/design-system/fixtures/posting-notices";
import type { BulkPreview } from "@/features/apply/api/bulk-apply-api";
import { noticesSchema } from "@/features/jobs/api/notices-api";
import { SwipeCard } from "@/features/opportunities/components/deck/swipe-card";
import { createDeckSignals } from "@/features/opportunities/hooks/deck/use-deck-signals";
import { makeCard } from "@/test/deck";

function show(node: React.ReactNode) {
  return renderWithQuery(<MemoryRouter>{node}</MemoryRouter>);
}

describe("PostingNotices", () => {
  it("says each notice in full, with the posting's own words and the other posting", () => {
    show(<PostingNotices notices={NOTICES} />);
    const list = screen.getByRole("list", { name: "Before you apply" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent(
      "This posting says it does not sponsor visas, and your profile says you need sponsorship in Germany.",
    );
    expect(items[0]).toHaveAttribute("data-level", "warning");
    expect(items[0]).toHaveTextContent(
      "“We are unable to offer visa sponsorship for this role.”",
    );
    expect(
      within(items[2]!).getByRole("link", { name: "Open the other posting" }),
    ).toHaveAttribute("href", `/jobs/${NOTICES[2]!.job_id!}`);
    expect(
      within(items[4]!).getByRole("link", { name: "Open that application" }),
    ).toHaveAttribute("href", `/jobs/${NOTICES[4]!.job_id!}`);
  });

  it("shows the first sentences alone when compact, and counts the rest", () => {
    show(<PostingNotices notices={NOTICES} compact max={2} />);
    const items = within(
      screen.getByRole("list", { name: "Before you apply" }),
    ).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      NOTICES[0]!.text,
      NOTICES[1]!.text,
      "3 more to know",
    ]);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText(/unable to offer/)).toBeNull();
  });

  it("shows nothing when there is nothing to know", () => {
    const { container } = show(<PostingNotices notices={[]} />);
    expect(container).toBeEmptyDOMElement();
    show(<PostingNotices notices={undefined} />);
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("reads the api's notices, and its words have no dash as a separator", () => {
    expect(noticesSchema.parse(NOTICES)).toEqual(NOTICES);
    for (const notice of NOTICES) {
      expect(notice.text).not.toMatch(/ - |—|–/);
    }
  });
});

describe("the Apply confirmation", () => {
  it("names what to know under each posting it applies to", async () => {
    const preview: BulkPreview = {
      review_before_send: false,
      limit: 25,
      items: [
        {
          posting_id: "00000000-0000-4000-9000-000000000001",
          company: "Example Robotics",
          title: "Robotics Engineer",
          documents: [],
          estimate: null,
          email: false,
          seen: "a".repeat(64),
          notices: [NOTICES[4]!],
        },
      ],
      left_out: [],
      beyond: [],
      estimate: null,
      cost_note: "Your provider's invoice is the exact figure.",
    };
    server.use(
      http.post("*/api/apply/bulk/preview", () => HttpResponse.json(preview)),
    );
    show(
      <BulkApplyDialog
        request={{ ids: [preview.items[0]!.posting_id], key: 1 }}
        onClose={() => undefined}
        onApplied={() => undefined}
      />,
    );
    const listed = await screen.findByRole("list", {
      name: "Postings to apply to",
    });
    expect(
      within(listed).getByRole("list", { name: "Before you apply" }),
    ).toHaveTextContent(NOTICES[4]!.text);
  });
});

describe("the Discover card", () => {
  it("says the first two things to know under the posting's facts", () => {
    show(
      <SwipeCard
        card={makeCard(1, { notices: NOTICES })}
        role="top"
        motion={false}
        interactive
        signals={createDeckSignals()}
        onSwipe={() => undefined}
        onMore={() => undefined}
      />,
    );
    const card = screen.getByTestId("deck-card");
    const items = within(
      within(card).getByRole("list", { name: "Before you apply" }),
    ).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      NOTICES[0]!.text,
      NOTICES[1]!.text,
      "3 more to know",
    ]);
  });
});
