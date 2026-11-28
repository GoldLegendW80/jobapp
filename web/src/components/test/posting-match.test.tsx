import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PostingMatch } from "@/components/shared/jobs/posting/posting-match";
import {
  MATCH,
  MATCH_LONGEST,
  MATCH_OLDER_TEXT,
} from "@/design-system/fixtures/posting-match";
import { jobDetailSchema } from "@/features/jobs/api/jobs-api";
import { matchBand } from "@/features/jobs/helpers/match-bands";
import { makeDetail } from "@/test/jobs";

describe("matchBand", () => {
  it.each([
    [100, "Strong"],
    [80, "Strong"],
    [79, "Good"],
    [60, "Good"],
    [59, "Partial"],
    [40, "Partial"],
    [39, "Weak"],
    [0, "Weak"],
  ])("reads %i as %s", (score, word) => {
    expect(matchBand(score)).toBe(word);
  });
});

describe("PostingMatch", () => {
  it("shows both scores with their label and band, and the lists", () => {
    render(<PostingMatch match={MATCH} />);
    expect(
      screen.getByRole("heading", { level: 2, name: "Career and culture" }),
    ).toBeInTheDocument();
    const career = screen.getByText("Career").closest("div")!;
    expect(career).toHaveTextContent("72");
    expect(career).toHaveTextContent("out of 100");
    expect(career).toHaveTextContent("Good");
    expect(screen.getByText("Culture").closest("div")).toHaveTextContent(
      "Strong",
    );
    expect(
      within(screen.getByRole("list", { name: "Strengths" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(2);
    expect(
      within(
        screen.getByRole("list", { name: "Gaps in the match" }),
      ).getAllByRole("listitem"),
    ).toHaveLength(1);
    expect(screen.getByText(/keyword fit/)).toBeInTheDocument();
    expect(screen.getByText(/Read on 27 Nov 2026/)).toBeInTheDocument();
    expect(screen.queryByText(/changed since/)).toBeNull();
  });

  it("keeps a reading on an older text with one line that says so", () => {
    render(<PostingMatch match={MATCH_OLDER_TEXT} />);
    expect(screen.getByText(/text changed since/)).toHaveTextContent(
      "will be read again",
    );
    expect(screen.getByText("Career")).toBeInTheDocument();
  });

  it("renders nothing without a reading", () => {
    const { container: none } = render(<PostingMatch match={null} />);
    expect(none).toBeEmptyDOMElement();
    const { container: absent } = render(<PostingMatch match={undefined} />);
    expect(absent).toBeEmptyDOMElement();
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("shows the model's words as text, never as markup", () => {
    const text = "<img src=x onerror=alert(1)> & <b>bold</b>";
    const { container } = render(
      <PostingMatch match={{ ...MATCH, strengths: [text] }} />,
    );
    expect(container.querySelector("img, b")).toBeNull();
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("shows the longest allowed lines", () => {
    render(<PostingMatch match={MATCH_LONGEST} />);
    expect(
      within(screen.getByRole("list", { name: "Strengths" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(3);
    expect(
      within(
        screen.getByRole("list", { name: "Gaps in the match" }),
      ).getAllByRole("listitem"),
    ).toHaveLength(3);
  });
});

describe("the detail's reading", () => {
  it("parses a reading", () => {
    const parsed = jobDetailSchema.parse(makeDetail({ match: MATCH }));
    expect(parsed.match?.career).toBe(72);
  });

  it("parses an older api's detail with no reading, which renders nothing", () => {
    const older: Partial<ReturnType<typeof makeDetail>> = makeDetail();
    delete older.match;
    const parsed = jobDetailSchema.parse(older);
    expect(parsed.match).toBeUndefined();
    const { container } = render(<PostingMatch match={parsed.match} />);
    expect(container).toBeEmptyDOMElement();
  });
});
