import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  FEW_INSIGHTS,
  INSIGHTS,
  NO_INSIGHTS,
} from "@/design-system/fixtures/insights";
import { admin, fakeSession } from "@/test/auth";
import { crumb } from "@/test/breadcrumb";
import { fakeInsights } from "@/test/insights";
import { fakeJobsApi } from "@/test/jobs";
import { renderApp } from "@/test/render";

const [ROBOTICS, LABS] = INSIGHTS.silent.map((row) => row.id);
/** The page and its chart load on demand: the first visit of a run may take a while. */
const WAIT = { timeout: 4000 };

const silentList = () =>
  screen.getByRole("list", { name: "Silent applications" });

beforeEach(() => {
  fakeSession(admin);
});

describe("Your search in numbers", () => {
  it("shows the funnel, the rates, the dated benchmarks and the groups", async () => {
    fakeInsights(INSIGHTS);
    renderApp("/jobs/insights");
    expect(
      await screen.findByRole(
        "heading",
        { level: 1, name: "Your search in numbers" },
        WAIT,
      ),
    ).toBeInTheDocument();
    expect(crumb("Jobs")).toHaveAttribute("href", "/jobs");
    expect(
      await screen.findByRole(
        "img",
        { name: /^Found: 412, Liked: 96, Applied: 74/ },
        WAIT,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText(/directional figures/)).toHaveTextContent(
      "HiringThing and scale.jobs, 2025",
    );
    const byFit = screen.getByRole("table", {
      name: "Applications and interviews by fit",
    });
    expect(
      within(byFit).getByRole("row", { name: /Fit 80 and above 28 15 8 29%/ }),
    ).toBeInTheDocument();
    expect(
      within(byFit).getByRole("row", { name: /No fit 4 0 0 Too few to say/ }),
    ).toBeInTheDocument();
    const bySite = screen.getByRole("table", {
      name: "Applications and interviews by application site",
    });
    expect(within(bySite).getByText("Workday")).toBeInTheDocument();
    const bySource = screen.getByRole("table", {
      name: "Applications and interviews by source",
    });
    expect(within(bySource).getByText("Job alert e-mails")).toBeInTheDocument();
    expect(within(bySource).getByText("Remote OK")).toBeInTheDocument();
    expect(screen.getByText("Pay too low")).toBeInTheDocument();
    expect(screen.getByText("9 days")).toBeInTheDocument();
  });

  it("says too few to say, with no benchmark, under 20 applications", async () => {
    fakeInsights(FEW_INSIGHTS);
    renderApp("/jobs/insights");
    expect(
      await screen.findByText(
        "Rates show from 20 applications. You have 4 applications so far.",
        {},
        WAIT,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/directional figures/)).not.toBeInTheDocument();
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument();
    expect(
      screen.getByText("No application has been silent that long."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Close/ }),
    ).not.toBeInTheDocument();
  });

  it("closes only the ticked silent applications, then undoes it", async () => {
    const fake = fakeInsights(INSIGHTS);
    fakeJobsApi([]);
    const user = userEvent.setup();
    renderApp("/jobs/insights");
    await screen.findByRole("list", { name: "Silent applications" }, WAIT);
    expect(
      screen.getByText(/1 application marked Applied has no date at all/),
    ).toBeInTheDocument();
    await user.click(
      within(silentList()).getByRole("checkbox", {
        name: "Machine Learning Engineer at Example Labs",
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Close 1 application" }),
    );
    await waitFor(() => {
      expect(fake.calls).toContainEqual({
        path: "POST close",
        body: { ids: [ROBOTICS] },
      });
    });
    expect(
      await screen.findByText("Closed 1 application."),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(
        within(silentList()).queryByText(/Example Robotics/),
      ).not.toBeInTheDocument();
    });
    // The unticked one stays, still unticked: the next click would not close it.
    expect(
      within(silentList()).getByRole("checkbox", {
        name: "Machine Learning Engineer at Example Labs",
      }),
    ).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => {
      expect(fake.calls).toContainEqual({
        path: "POST undo",
        body: { token: "undo-token" },
      });
    });
    expect(
      await within(silentList()).findByText(/Example Robotics/),
    ).toBeInTheDocument();
    expect(LABS).toBeDefined();
  });

  it("says when an application changed since and stayed open", async () => {
    const fake = fakeInsights(INSIGHTS);
    fake.stillSilent = new Set([ROBOTICS ?? ""]);
    fakeJobsApi([]);
    const user = userEvent.setup();
    renderApp("/jobs/insights");
    await user.click(
      await screen.findByRole("button", { name: "Close 2 applications" }, WAIT),
    );
    expect(
      await screen.findByText(
        "Closed 1 application. 1 application changed since and stayed open.",
      ),
    ).toBeInTheDocument();
  });

  it("offers Jobs when there is nothing to count", async () => {
    fakeInsights(NO_INSIGHTS);
    renderApp("/jobs/insights");
    expect(
      await screen.findByText("Nothing to count yet", {}, WAIT),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Jobs" })).toHaveAttribute(
      "href",
      "/jobs",
    );
  });

  it("says what failed and retries", async () => {
    const fake = fakeInsights(INSIGHTS);
    fake.failWith = 500;
    const user = userEvent.setup();
    renderApp("/jobs/insights");
    expect(
      await screen.findByText(/Your numbers could not be loaded/, {}, WAIT),
    ).toBeInTheDocument();
    fake.failWith = null;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("list", { name: "Silent applications" }),
    ).toBeInTheDocument();
  });
});
