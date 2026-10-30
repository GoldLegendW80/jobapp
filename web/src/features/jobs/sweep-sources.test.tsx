import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { SweepSources } from "./sweep-sources";
import type { SweepSource } from "./sweeps-api";

function source(fields: Partial<SweepSource>): SweepSource {
  return {
    kind: "linkedin",
    query: "",
    location: null,
    host: "www.linkedin.com",
    result_pages: 1,
    found: 0,
    new: 0,
    skipped_by_rule: 0,
    items_skipped: 0,
    error: null,
    ...fields,
  };
}

// LinkedIn searched through its public job search (owner, 2026-10-30) is one of the job sites;
// a deleted company board's entry (mode api, any other kind) stays out.
it("lists LinkedIn's public job search with its searches and leaves a deleted board out", () => {
  render(
    <SweepSources
      sources={[
        source({
          mode: "api",
          phase: "done",
          outcome: "complete",
          found: 3,
          new: 2,
          searches: 1,
          searched: [{ title: "GenAI Engineer", place: null }],
        }),
        source({ mode: "api", kind: "ashby", host: null, found: 9, new: 9 }),
      ]}
    />,
  );
  const items = screen.getAllByRole("listitem");
  expect(items).toHaveLength(1);
  expect(items[0]).toHaveTextContent(
    "LinkedIn: 1 search ran: GenAI Engineer. 3 postings found, 2 new.",
  );
});

it("says in plain words that LinkedIn asked the app to slow down", () => {
  render(
    <SweepSources
      sources={[
        source({
          mode: "api",
          phase: "done",
          outcome: "partial",
          reason: "rate_limited",
          error: "rate_limited",
          found: 1,
          new: 1,
        }),
        source({
          mode: "api",
          phase: "done",
          reason: "linkedin_searched_once",
          error: "linkedin_searched_once",
          result_pages: 0,
        }),
      ]}
    />,
  );
  const [first, second] = screen.getAllByRole("listitem");
  expect(first).toHaveTextContent(
    "LinkedIn asked the app to slow down, so its search stopped for this time.",
  );
  expect(second).toHaveTextContent(
    "LinkedIn was searched once through its public job search for all your LinkedIn addresses.",
  );
  for (const item of [first, second])
    expect(item?.textContent).not.toMatch(/[–—]| - /);
});

it("says a LinkedIn public search not run yet was not searched this time", () => {
  render(
    <SweepSources
      sources={[source({ mode: "api", phase: "pending", result_pages: 0 })]}
    />,
  );
  expect(screen.getByRole("listitem")).toHaveTextContent(
    "LinkedIn: not searched this time.",
  );
});
