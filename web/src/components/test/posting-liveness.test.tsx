import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { LivenessBadge } from "@/components/shared/jobs/liveness-badge";
import { PostingLiveness } from "@/components/shared/jobs/posting/posting-liveness";
import {
  checkedPosting,
  CLOSED_CHECK,
  MAY_BE_CLOSED_CHECK,
  OPEN_CHECK,
} from "@/design-system/fixtures/liveness";
import { SwipeCard } from "@/features/opportunities/components/deck/swipe-card";
import { createDeckSignals } from "@/features/opportunities/hooks/deck/use-deck-signals";
import { makeCard } from "@/test/deck";
import { renderWithQuery } from "@/test/render";
import { server } from "@/test/server";

const QUOTE = "“Job expired”";

describe("PostingLiveness", () => {
  it("shows a proposal with the page's words, and Mark closed closes it on the version shown", async () => {
    const job = checkedPosting(MAY_BE_CLOSED_CHECK);
    const sent: unknown[] = [];
    server.use(
      http.patch("*/api/jobs/:id", async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ ...job, status: "closed" });
      }),
    );
    renderWithQuery(<PostingLiveness job={job} />);
    expect(screen.getByTestId("liveness-words")).toHaveTextContent(
      "Checked on 7 Nov 2026: its page may say it is closed.",
    );
    expect(screen.getByText(QUOTE)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Mark closed" }));
    await waitFor(() => {
      expect(sent).toEqual([{ status: "closed", updated_at: job.updated_at }]);
    });
  });

  it("asks the page again on Check now and shows what it found", async () => {
    const job = checkedPosting(null);
    const calls = vi.fn();
    server.use(
      http.post("*/api/jobs/:id/check", ({ params }) => {
        calls(params.id);
        return HttpResponse.json(checkedPosting(OPEN_CHECK));
      }),
    );
    const { client } = renderWithQuery(<PostingLiveness job={job} />);
    expect(screen.getByTestId("liveness-words")).toHaveTextContent(
      "Not checked yet.",
    );
    expect(
      screen.queryByRole("button", { name: "Mark closed" }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() => {
      expect(calls).toHaveBeenCalledWith(job.id);
    });
    await waitFor(() => {
      expect(client.getQueryData(["jobs", "detail", job.id])).toMatchObject({
        liveness: OPEN_CHECK,
      });
    });
  });

  it("says when a check fails, and offers no Mark closed for a posting the page closed", async () => {
    server.use(
      http.post("*/api/jobs/:id/check", () =>
        HttpResponse.json({ detail: "This posting is busy." }, { status: 409 }),
      ),
    );
    renderWithQuery(
      <PostingLiveness
        job={checkedPosting(CLOSED_CHECK, { status: "closed" })}
      />,
    );
    expect(screen.getByTestId("liveness-words")).toHaveTextContent(
      "its page says it is closed, so it moved to Closed.",
    );
    expect(
      screen.queryByRole("button", { name: "Mark closed" }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This posting is busy.",
    );
  });

  it("shows nothing for a posting without an address, and no Check now on LinkedIn", () => {
    const { rerender } = renderWithQuery(
      <PostingLiveness job={checkedPosting(null, { url: null })} />,
    );
    expect(screen.queryByText("Still open?")).not.toBeInTheDocument();
    rerender(
      <PostingLiveness
        job={checkedPosting(
          { ...OPEN_CHECK, verdict: "unconfirmed", reason: "linkedin" },
          { url: "https://www.linkedin.com/jobs/view/4012345678/" },
        )}
      />,
    );
    expect(screen.getByText("Still open?")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Check now" }),
    ).not.toBeInTheDocument();
  });
});

describe("LivenessBadge", () => {
  it("says May be closed on an open posting only", () => {
    const { rerender } = renderWithQuery(
      <LivenessBadge liveness={MAY_BE_CLOSED_CHECK} status="found" />,
    );
    expect(screen.getByTestId("liveness-badge")).toHaveTextContent(
      "May be closed",
    );
    rerender(<LivenessBadge liveness={MAY_BE_CLOSED_CHECK} status="applied" />);
    expect(screen.queryByTestId("liveness-badge")).not.toBeInTheDocument();
    rerender(<LivenessBadge liveness={OPEN_CHECK} status="found" />);
    expect(screen.queryByTestId("liveness-badge")).not.toBeInTheDocument();
  });

  it("shows on a Discover card whose page may say it is closed", () => {
    const props = {
      role: "top" as const,
      motion: false,
      interactive: true,
      signals: createDeckSignals(),
      onSwipe: vi.fn(),
      onMore: vi.fn(),
    };
    const { rerender } = renderWithQuery(
      <SwipeCard
        {...props}
        card={makeCard(1, { liveness: MAY_BE_CLOSED_CHECK })}
      />,
    );
    expect(screen.getByTestId("liveness-badge")).toHaveTextContent(
      "May be closed",
    );
    rerender(<SwipeCard {...props} card={makeCard(1)} />);
    expect(screen.queryByTestId("liveness-badge")).not.toBeInTheDocument();
  });
});
