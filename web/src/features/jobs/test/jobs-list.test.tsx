import { fireEvent, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeDetail, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

/** The items of the list, in order. */
const items = () =>
  within(screen.getByRole("list", { name: "Postings" }))
    .getAllByRole("listitem")
    .filter((item) => item.hasAttribute("data-index"));

beforeEach(() => {
  fakeSession(admin);
  // The detail pane beside the list opens on the first posting.
  server.use(
    http.get("*/api/jobs/:id", ({ params }) =>
      String(params.id).startsWith("00000000-0000-4000-9000-")
        ? HttpResponse.json(makeDetail())
        : undefined,
    ),
  );
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  fireEvent.scroll(window);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function openJobs() {
  renderApp("/jobs");
  await within(
    await screen.findByRole("list", { name: "Postings" }),
  ).findAllByRole("listitem");
}

describe("a posting in the jobs list", () => {
  it("is a compact card: the company's initial, the title, the company, the place, the pay, the fit and the status", async () => {
    fakeJobsApi([
      makeJob(1, {
        title: "Senior data engineer",
        company: "lumen Freight",
        location: "Lyon, France",
        work_mode: "hybrid",
        contract_type: "CDI",
        status: "shortlisted",
        fit: 91,
        pay_min: 55000,
        pay_max: 65000,
        pay_currency: "EUR",
        pay_period: "year",
      }),
    ]);
    await openJobs();
    const [card] = items();
    expect(
      card!.querySelector("[data-slot=avatar-fallback]"),
    ).toHaveTextContent(/^L$/);
    expect(
      within(card!).getByRole("link", { name: "Senior data engineer" }),
    ).toHaveClass("font-medium");
    expect(within(card!).getByText("lumen Freight")).toBeInTheDocument();
    expect(
      within(card!).getByText("Lyon, France, Hybrid, Permanent"),
    ).toBeInTheDocument();
    expect(
      within(card!).getByText("55,000 to 65,000 EUR a year"),
    ).toBeInTheDocument();
    expect(within(card!).getByLabelText("Fit 91 out of 100")).toBeVisible();
    expect(within(card!).getByText("Shortlisted")).toBeVisible();
    expect(
      within(card!).getByRole("checkbox", {
        name: "Select Senior data engineer at lumen Freight",
      }),
    ).toBeInTheDocument();
    // Never a table: the list reads as a list of postings.
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("joins the place, the work mode and the contract with commas, never a dot or a dash, and says a mode once", async () => {
    fakeJobsApi([
      makeJob(1, {
        location: "Remote, France",
        work_mode: "remote",
        contract_type: "Freelance",
      }),
      makeJob(2, {
        location: "Grenoble, France",
        work_mode: "office",
        contract_type: "CDD",
      }),
      makeJob(3, { location: "", work_mode: null, contract_type: "VIE" }),
    ]);
    await openJobs();
    const [a, b, c] = items();
    expect(
      within(a!).getByText("Remote, France, Freelance mission"),
    ).toBeInTheDocument();
    expect(
      within(b!).getByText("Grenoble, France, On site, Fixed term"),
    ).toBeInTheDocument();
    expect(within(c!).getByText("VIE")).toBeInTheDocument();
    for (const card of items())
      expect(card.textContent).not.toMatch(/[\u00b7\u2013\u2014]| - /);
  });

  it("shows the pay with separators, and Pay not given in a muted style when none", async () => {
    fakeJobsApi([
      makeJob(1, {
        pay_min: 55000,
        pay_max: 1250000,
        pay_currency: "EUR",
        pay_period: "year",
      }),
      makeJob(2, {
        pay_min: 600,
        pay_max: 600,
        pay_currency: "EUR",
        pay_period: "day",
      }),
      makeJob(3),
    ]);
    await openJobs();
    const [a, b, c] = items();
    const pay = (card: HTMLElement) =>
      card.querySelector<HTMLElement>("[data-pay]")!;
    expect(pay(a!)).toHaveTextContent(/^55,000 to 1,250,000 EUR a year$/);
    expect(pay(a!)).not.toHaveClass("text-muted-foreground");
    expect(pay(b!)).toHaveTextContent(/^600 EUR a day$/);
    expect(pay(c!)).toHaveTextContent(/^Pay not given$/);
    expect(pay(c!)).toHaveClass("text-muted-foreground");
  });

  it("says the contract in words, never as its code, and keeps an unknown one as it came", async () => {
    fakeJobsApi([
      makeJob(1, { contract_type: "CDI", location: "" }),
      makeJob(2, { contract_type: "Freelance", location: "" }),
      makeJob(3, { contract_type: "Alternance", location: "" }),
      makeJob(4, { contract_type: "Interim", location: "" }),
    ]);
    await openJobs();
    const words = [
      "Permanent",
      "Freelance mission",
      "Apprenticeship",
      "Interim",
    ];
    items().forEach((card, index) => {
      expect(within(card).getByText(words[index]!)).toBeInTheDocument();
    });
    expect(
      screen.getByRole("list", { name: "Postings" }),
    ).not.toHaveTextContent(/\bCDI\b/);
  });

  it("says the fit in words next to its colour", async () => {
    fakeJobsApi([
      makeJob(1, { fit: 13 }),
      makeJob(2, { fit: 42, fit_overridden: true }),
      makeJob(3, { fit: null }),
    ]);
    await openJobs();
    const fit = (card: HTMLElement) =>
      card.querySelector<HTMLElement>("[data-tone]")!;
    const [a, b, c] = items();
    expect(fit(a!)).toHaveTextContent(/^Fit 13$/);
    expect(fit(b!)).toHaveTextContent(/^Fit 42manual$/);
    expect(fit(c!)).toHaveTextContent(/^Unscored$/);
  });
});
