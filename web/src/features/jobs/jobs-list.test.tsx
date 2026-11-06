import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeJobsApi, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";

/** The rows of the list, in order, with the columns read by their header. */
const table = () => screen.getByRole("table", { name: "Postings" });
const dataRows = () =>
  within(table())
    .getAllByRole("row")
    .filter((row) => row.hasAttribute("data-index"));
const headers = () =>
  within(table())
    .getAllByRole("columnheader")
    .map((cell) => cell.textContent);
/** The cell of `row` under the column named `name`. */
function cellOf(row: HTMLElement, name: string): HTMLElement {
  const index = headers().indexOf(name);
  expect(index).toBeGreaterThan(-1);
  return within(row).getAllByRole("cell")[index]!;
}

beforeEach(() => {
  fakeSession(admin);
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  fireEvent.scroll(window);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

async function openJobs() {
  renderApp("/jobs");
  await within(
    await screen.findByRole("table", { name: "Postings" }),
  ).findAllByRole("row");
}

describe("the jobs list's pay, type and fit", () => {
  it("has a Pay column between the place and the type", async () => {
    fakeJobsApi([makeJob(1)]);
    await openJobs();
    expect(headers()).toEqual([
      "",
      "Company",
      "Title",
      "Location",
      "Pay",
      "Type",
      "Fit",
      "Status",
      "Found",
      "Apply",
    ]);
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
    const [a, b, c] = dataRows();
    expect(cellOf(a!, "Pay")).toHaveTextContent(
      /^55,000 to 1,250,000 EUR a year$/,
    );
    expect(within(cellOf(a!, "Pay")).getByText(/EUR/)).not.toHaveClass(
      "text-muted-foreground",
    );
    expect(cellOf(b!, "Pay")).toHaveTextContent(/^600 EUR a day$/);
    expect(cellOf(c!, "Pay")).toHaveTextContent(/^Pay not given$/);
    expect(within(cellOf(c!, "Pay")).getByText("Pay not given")).toHaveClass(
      "text-muted-foreground",
    );
  });

  it("says the contract in words, never as its code, and keeps an unknown one as it came", async () => {
    fakeJobsApi([
      makeJob(1, { contract_type: "CDI" }),
      makeJob(2, { contract_type: "Freelance" }),
      makeJob(3, { contract_type: "Alternance" }),
      makeJob(4, { contract_type: "Interim" }),
    ]);
    await openJobs();
    expect(dataRows().map((row) => cellOf(row, "Type").textContent)).toEqual([
      "Permanent",
      "Freelance mission",
      "Apprenticeship",
      "Interim",
    ]);
    expect(table()).not.toHaveTextContent(/\bCDI\b/);
  });

  it("says the fit in words next to its colour", async () => {
    fakeJobsApi([
      makeJob(1, { fit: 13 }),
      makeJob(2, { fit: 42, fit_overridden: true }),
      makeJob(3, { fit: null }),
    ]);
    await openJobs();
    const [a, b, c] = dataRows();
    expect(cellOf(a!, "Fit")).toHaveTextContent(/^Fit 13$/);
    expect(cellOf(b!, "Fit")).toHaveTextContent(/^Fit 42manual$/);
    expect(cellOf(c!, "Fit")).toHaveTextContent(/^Unscored$/);
  });
});
