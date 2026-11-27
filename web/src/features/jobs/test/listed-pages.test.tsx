import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ListedMention } from "@/components/shared/jobs/listed-mention";
import {
  LISTED_GIVEN_UP,
  LISTED_PAGES_ENDS,
  listedPagesSentence,
} from "@/features/jobs/helpers/listed-words";
import { renderWithQuery } from "@/test/render";

const pages = (overrides = {}) => ({
  read: 0,
  closed: 0,
  unread: 0,
  given_up: 0,
  left: 0,
  ended: "done",
  ...overrides,
});

describe("Jobs J36: the pages of the postings seen in a list, read after the search", () => {
  it("says what the app read, closed and could not read, what waits and why", () => {
    expect(
      listedPagesSentence(
        pages({
          read: 12,
          closed: 1,
          unread: 3,
          given_up: 1,
          left: 30,
          ended: "browser_wanted",
        }),
      ),
    ).toBe(
      "After the search, the app opened the pages of 16 postings seen only in a list: 12 read, 1 closed, as gone, 3 could not be read (1 no longer tried). 30 postings seen only in a list still wait for their page: an application needed the browser.",
    );
    expect(listedPagesSentence(pages({ read: 1 }))).toBe(
      "After the search, the app opened the pages of 1 posting seen only in a list: 1 read.",
    );
    expect(listedPagesSentence(pages({ left: 1, ended: "walled" }))).toBe(
      "1 posting seen only in a list still waits for its page: the website showed a sign in page or a security check.",
    );
    expect(listedPagesSentence(pages({ left: 2, ended: "no_tab" }))).toBe(
      "2 postings seen only in a list still wait for their page: the browser could not open a tab for the app.",
    );
  });

  it("says nothing when nothing was there, and reads an end it does not know as a problem", () => {
    expect(listedPagesSentence(pages())).toBe("");
    expect(listedPagesSentence(null)).toBe("");
    expect(listedPagesSentence(undefined)).toBe("");
    expect(
      listedPagesSentence(pages({ left: 2, ended: "toString" })),
    ).toContain(LISTED_PAGES_ENDS.failed ?? "");
  });

  it("has no dash as a separator in any of its words", () => {
    for (const words of [
      LISTED_GIVEN_UP,
      ...Object.values(LISTED_PAGES_ENDS),
    ]) {
      expect(words).not.toMatch(/ - |–|—/);
    }
  });

  it("the mention says when the app no longer tries to read its page", () => {
    const job = {
      list_url: "https://jobs.example.org/search?q=robot",
      url: "https://jobs.example.org/jobs/1",
    };
    const { rerender } = renderWithQuery(<ListedMention job={job} />);
    expect(screen.queryByText(LISTED_GIVEN_UP, { exact: false })).toBeNull();
    rerender(<ListedMention job={{ ...job, page_given_up: true }} />);
    expect(screen.getByTestId("listed-mention")).toHaveTextContent(
      LISTED_GIVEN_UP,
    );
  });
});
