import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { LoadError } from "@/components/blocks/load-error";
import { PageHeader } from "@/components/blocks/page-header";
import { PageTabs } from "@/components/blocks/page-tabs";
import { TooltipProvider } from "@/components/ui/tooltip";

function Where() {
  const { pathname, search } = useLocation();
  return <output aria-label="address">{pathname + search}</output>;
}

function JobsViews() {
  return (
    <>
      <PageTabs
        label="Jobs views"
        tabs={[
          { to: "/jobs?status=found", label: "List" },
          { to: "/jobs/map?status=found", label: "Map" },
        ]}
      >
        <p>The open view</p>
      </PageTabs>
      <Where />
    </>
  );
}

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<JobsViews />} />
      </Routes>
    </MemoryRouter>,
  );

describe("the views of a page", () => {
  it("marks the view of the address and holds its content in the tab panel", () => {
    at("/jobs/map");
    expect(screen.getByRole("tab", { name: "Map" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tabpanel")).toHaveTextContent("The open view");
  });

  it("opens a view at its address, query included, and only on Enter after the arrows", async () => {
    const user = userEvent.setup();
    at("/jobs");
    await user.click(screen.getByRole("tab", { name: "Map" }));
    expect(screen.getByLabelText("address")).toHaveTextContent(
      "/jobs/map?status=found",
    );

    await user.keyboard("{ArrowLeft}");
    expect(screen.getByLabelText("address")).toHaveTextContent("/jobs/map");
    await user.keyboard("{Enter}");
    expect(screen.getByLabelText("address")).toHaveTextContent(
      "/jobs?status=found",
    );
  });
});

function Arrived() {
  const location = useLocation();
  return (
    <output aria-label="arrived">
      {location.pathname} {JSON.stringify(location.state as unknown)}
    </output>
  );
}

const LONG =
  "CV for Data platform engineer, streaming and real time pricing at Atelier Brume";

describe("the breadcrumb of a page below others", () => {
  const renderHeader = () =>
    render(
      <MemoryRouter initialEntries={["/jobs/1/documents/cv"]}>
        <TooltipProvider>
          <Routes>
            <Route
              path="/jobs/1/documents/cv"
              element={
                <PageHeader
                  trail={[
                    { to: "/jobs", label: "Jobs", state: { from: "?q=data" } },
                    { to: "/jobs/1", label: "Data platform engineer" },
                  ]}
                  title={LONG}
                />
              }
            />
            <Route path="*" element={<Arrived />} />
          </Routes>
        </TooltipProvider>
      </MemoryRouter>,
    );

  it("links the pages above from the top, then names this page, whole in its title", () => {
    renderHeader();
    const trail = screen.getByRole("navigation", { name: "Breadcrumb" });
    const links = within(trail).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Jobs",
      "Data platform engineer",
      LONG,
    ]);
    expect(links[1]).toHaveAttribute("href", "/jobs/1");
    const current = within(trail).getByText(LONG);
    expect(current).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("heading", { level: 1, name: LONG }),
    ).toBeInTheDocument();
  });

  it("carries a step's state, the jobs list's query, to the page it opens", async () => {
    renderHeader();
    await userEvent.click(
      within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole(
        "link",
        { name: "Jobs" },
      ),
    );
    expect(screen.getByLabelText("arrived")).toHaveTextContent(
      '/jobs {"from":"?q=data"}',
    );
  });

  it("draws no breadcrumb on a page at the top", () => {
    render(
      <MemoryRouter>
        <PageHeader title="Jobs" />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

describe("a load error", () => {
  it("says what failed and why, and retries the query", async () => {
    const refetch = vi.fn();
    render(
      <LoadError
        query={{
          error: new Error("The server did not answer."),
          isFetching: false,
          refetch,
        }}
      >
        The postings could not be loaded.
      </LoadError>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The postings could not be loaded.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("cannot retry while the query is already fetching", () => {
    render(
      <LoadError
        query={{ error: new Error("x"), isFetching: true, refetch: vi.fn() }}
      >
        The postings could not be loaded.
      </LoadError>,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
  });
});
