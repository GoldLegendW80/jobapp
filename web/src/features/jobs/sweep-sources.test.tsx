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
          reason: "linkedin_rate_limited",
          error: "linkedin_rate_limited",
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

// Merged with the company boards, job feeds and alert e-mails (owner, 2026-10-30): LinkedIn's
// public job search stays among the job sites; a board and a feed, also mode api, keep their own
// blocks.
it("keeps LinkedIn's public job search among the job sites beside boards and feeds", () => {
  render(
    <SweepSources
      sources={[
        source({ mode: "api", phase: "done", outcome: "complete", found: 2 }),
        source({
          mode: "api",
          kind: "board",
          host: "boards-api.greenhouse.io",
          query: "greenhouse:examplelabs",
          company: "Example Labs",
          found: 4,
          new: 1,
        }),
        source({
          mode: "api",
          kind: "arbeitnow",
          host: "www.arbeitnow.com",
          found: 5,
          new: 3,
        }),
        source({ mode: "mail", kind: "alerts", host: "mail:indeed", found: 1 }),
      ]}
    />,
  );
  const sites = screen.getByRole("region", {
    name: "Job sites in your browser",
  });
  expect(sites).toHaveTextContent("LinkedIn: ");
  expect(sites).not.toHaveTextContent("Example Labs");
  expect(sites).not.toHaveTextContent("Arbeitnow");
  expect(
    screen.getByRole("region", { name: "Company job boards" }),
  ).toHaveTextContent("Example Labs: 4 postings matched, 1 new.");
  expect(screen.getByRole("region", { name: "Job feeds" })).toHaveTextContent(
    "Arbeitnow",
  );
});
