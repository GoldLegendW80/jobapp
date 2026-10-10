import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeJobDetail, fakeJobsApi, makeDetail, makeJob } from "@/test/jobs";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import type { PlacesMap } from "./places-api";

const id = (n: number) =>
  `00000000-0000-4000-b000-${String(n).padStart(12, "0")}`;

const MAP: PlacesMap = {
  places: [
    {
      name: "Paris",
      kind: "city",
      lat: 48.86,
      lon: 2.35,
      count: 9,
      counts_by_status: { found: 6, applied: 3 },
      job_ids: [id(1), id(2)],
      job_ids_truncated: true,
    },
    {
      name: "London",
      kind: "city",
      lat: 51.51,
      lon: -0.13,
      count: 1,
      counts_by_status: { applied: 1 },
      job_ids: [id(3)],
      job_ids_truncated: false,
    },
    {
      name: "United States",
      kind: "country",
      lat: 39.5,
      lon: -98.4,
      count: 2,
      counts_by_status: { shortlisted: 1, skipped: 1 },
      job_ids: [id(4), id(5)],
      job_ids_truncated: false,
    },
  ],
  jobs: {
    [id(1)]: {
      title: "Applied AI Engineer",
      company: "Mistral AI",
      location: "Paris, France",
      status: "found",
      fit: 91,
    },
    [id(2)]: {
      title: "Forward Deployed Engineer",
      company: "Dust",
      location: "Paris",
      status: "applied",
      fit: null,
    },
    [id(3)]: {
      title: "Solutions Engineer",
      company: "Anthropic",
      location: "London, UK",
      status: "applied",
      fit: 80,
    },
    [id(4)]: {
      title: "AI Engineer",
      company: "Acme",
      location: "Remote, United States",
      status: "shortlisted",
      fit: 70,
    },
    [id(5)]: {
      title: "ML Engineer",
      company: "Acme",
      location: "USA",
      status: "skipped",
      fit: 10,
    },
  },
  unplaced: [
    { location: "Remote", count: 4 },
    { location: "", count: 1 },
    { location: "東京", count: 1 },
  ],
  unplaced_locations: 3,
  total: 17,
  placed: 11,
  unplaced_total: 6,
};

function fakePlaces(
  answer: (params: URLSearchParams) => Response = () => HttpResponse.json(MAP),
) {
  const requests: URLSearchParams[] = [];
  server.use(
    http.get("*/api/jobs/places", ({ request }) => {
      const params = new URL(request.url).searchParams;
      requests.push(params);
      return answer(params);
    }),
  );
  return requests;
}

const pin = (name: string) =>
  screen.getByRole("button", {
    name: new RegExp(`^${name}: `),
    pressed: false,
  });
const mapGroup = () => screen.getByRole("group", { name: "World map" });

async function openMap(path = "/jobs/map") {
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "Jobs map" });
  await screen.findByRole("list", { name: "Places, most postings first" });
  return app;
}

beforeEach(() => {
  fakeSession(admin);
});

describe("map page", () => {
  it("shows a pin per place, labelled and sized by its postings, with the status line", async () => {
    fakePlaces();
    await openMap();
    expect(screen.getByText("11 of 17 postings on the map")).toHaveAttribute(
      "role",
      "status",
    );
    const paris = within(mapGroup()).getByRole("button", {
      name: "Paris: 9 postings, 6 Found, 3 Applied",
    });
    const london = within(mapGroup()).getByRole("button", {
      name: "London: 1 posting, 1 Applied",
    });
    expect(paris).toHaveAttribute("data-status", "found");
    expect(london).toHaveAttribute("data-status", "applied");
    const dot = (el: HTMLElement) =>
      parseFloat((el.firstElementChild as HTMLElement).style.width);
    expect(dot(paris)).toBeGreaterThan(dot(london));
    // Paris lies right of London and above the United States.
    const left = (name: string) =>
      parseFloat(
        within(mapGroup()).getByRole("button", {
          name: new RegExp(`^${name}:`),
        }).style.left,
      );
    expect(left("Paris")).toBeGreaterThan(left("London"));
    expect(left("United States")).toBeLessThan(left("London"));
    // The legend names the dominant statuses present.
    const legend = screen.getByRole("list", { name: /^Pin colours/ });
    expect(
      within(legend)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["Found", "Shortlisted", "Applied"]);
  });

  it("draws the land once the outlines are loaded", async () => {
    fakePlaces();
    await openMap();
    await waitFor(() => {
      expect(
        screen.getByTestId("land").getAttribute("d")?.length ?? 0,
      ).toBeGreaterThan(10_000);
    });
  });

  it("shows a tooltip on hover and on focus", async () => {
    fakePlaces();
    const user = userEvent.setup();
    await openMap();
    await user.hover(pin("Paris"));
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Paris");
    expect(tip).toHaveTextContent("9 postings, 6 Found, 3 Applied");
    expect(pin("Paris")).toHaveAttribute("aria-describedby", tip.id);
    await user.unhover(pin("Paris"));
    expect(screen.queryByRole("tooltip")).toBeNull();

    act(() => {
      pin("London").focus();
    });
    expect(screen.getByRole("tooltip")).toHaveTextContent("London");
    fireEvent.keyDown(pin("London"), { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("opens a place's postings from its pin, links to the details, and closes again", async () => {
    fakePlaces();
    const user = userEvent.setup();
    await openMap("/jobs/map?status=found&status=applied");
    await user.click(pin("Paris"));
    const panel = screen.getByRole("complementary", { name: "Paris" });
    expect(
      within(panel).getByText("9 postings, 6 Found, 3 Applied"),
    ).toBeVisible();
    const links = within(
      within(panel).getByRole("list", { name: "Postings in Paris" }),
    ).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      `/jobs/${id(1)}`,
      `/jobs/${id(2)}`,
    ]);
    expect(links[0]).toHaveTextContent("Applied AI Engineer");
    expect(links[0]).toHaveTextContent("Mistral AI · Paris, France");
    expect(links[1]).toHaveTextContent("Unscored");
    expect(panel).toHaveTextContent(
      "Showing the first 2 of 9. Narrow the filters to see the others.",
    );
    expect(
      within(mapGroup()).getByRole("button", {
        name: /^Paris:/,
        pressed: true,
      }),
    ).toBeInTheDocument();

    // A second click on the pin closes it; so does "All places".
    await user.click(
      within(mapGroup()).getByRole("button", {
        name: /^Paris:/,
        pressed: true,
      }),
    );
    expect(screen.getByRole("complementary", { name: "Places" })).toBeVisible();
    await user.click(
      within(
        screen.getByRole("list", { name: "Places, most postings first" }),
      ).getByRole("button", { name: /^United States:/ }),
    );
    expect(
      screen.getByRole("complementary", { name: "United States" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "All places" }));
    expect(screen.getByRole("complementary", { name: "Places" })).toBeVisible();
  });

  it("a posting opened from the map comes back to the map with its filters", async () => {
    // The detail handler first: the newer places handler must win over /api/jobs/:id.
    fakeJobDetail(makeDetail({ id: id(1), title: "Applied AI Engineer" }));
    fakePlaces();
    const user = userEvent.setup();
    const app = await openMap("/jobs/map?status=found");
    await user.click(pin("Paris"));
    await user.click(screen.getByRole("link", { name: /Applied AI Engineer/ }));
    await screen.findByRole("heading", {
      level: 1,
      name: "Applied AI Engineer",
    });
    const back = screen.getByRole("link", { name: "Back to jobs" });
    expect(back).toHaveAttribute("href", "/jobs/map?status=found");
    await user.click(back);
    await waitFor(() => {
      expect(app.location()).toBe("/jobs/map?status=found");
    });
  });

  it("lists every place as a button with its count: the list alternative to the map", async () => {
    fakePlaces();
    await openMap();
    const list = screen.getByRole("list", {
      name: "Places, most postings first",
    });
    const buttons = within(list).getAllByRole("button");
    expect(buttons.map((b) => b.getAttribute("aria-label"))).toEqual([
      "Paris: 9 postings, 6 Found, 3 Applied",
      "London: 1 posting, 1 Applied",
      "United States: 2 postings, 1 Shortlisted, 1 Skipped",
    ]);
    expect(
      screen.getByRole("complementary", { name: "Places" }),
    ).toHaveTextContent("3 places, 11 of 17 postings placed");
  });

  it("lists the unplaced locations with their counts", async () => {
    fakePlaces();
    await openMap();
    const section = screen.getByRole("region", { name: "Unplaced" });
    expect(section).toHaveTextContent("6 postings without a place on the map.");
    const items = within(
      within(section).getByRole("list", { name: "Unplaced locations" }),
    ).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "Remote4",
      "Location not stated1",
      "東京1",
    ]);
  });

  it("says when the unplaced list holds only the most frequent locations", async () => {
    const many = Array.from({ length: 200 }, (_, i) => ({
      location: `Nowhere ${String(i).padStart(3, "0")}`,
      count: 1,
    }));
    fakePlaces(() =>
      HttpResponse.json({
        ...MAP,
        unplaced: many,
        unplaced_locations: 1234,
        unplaced_total: 1500,
        total: 1511,
      }),
    );
    await openMap();
    const section = screen.getByRole("region", { name: "Unplaced" });
    expect(section).toHaveTextContent(
      "1500 postings without a place on the map.",
    );
    expect(section).toHaveTextContent(
      "The 200 most frequent of 1234 locations.",
    );
    expect(
      within(
        within(section).getByRole("list", { name: "Unplaced locations" }),
      ).getAllByRole("listitem"),
    ).toHaveLength(200);
  });

  it("does not mention a cap when every unplaced location is listed", async () => {
    fakePlaces();
    await openMap();
    expect(
      screen.getByRole("region", { name: "Unplaced" }),
    ).not.toHaveTextContent("most frequent");
  });

  it("has no unplaced section and says so when nothing is placed", async () => {
    fakePlaces(() =>
      HttpResponse.json({
        places: [],
        jobs: {},
        unplaced: [],
        unplaced_locations: 0,
        total: 0,
        placed: 0,
        unplaced_total: 0,
      }),
    );
    renderApp("/jobs/map");
    expect(
      await screen.findByText("No posting in this view names a known place."),
    ).toBeVisible();
    expect(screen.queryByRole("region", { name: "Unplaced" })).toBeNull();
    expect(within(mapGroup()).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("list", { name: /^Pin colours/ })).toBeNull();
  });

  it("zooms and pans with the keyboard on the focused map", async () => {
    fakePlaces();
    await openMap();
    const map = mapGroup();
    const svg = screen.getByTestId("world-svg");
    act(() => {
      map.focus();
    });
    expect(map).toHaveAttribute("data-zoom", "1.00");
    fireEvent.keyDown(map, { key: "+" });
    expect(map).toHaveAttribute("data-zoom", "1.50");
    fireEvent.keyDown(map, { key: "=" });
    expect(map).toHaveAttribute("data-zoom", "2.25");
    const before = svg.getAttribute("viewBox");
    fireEvent.keyDown(map, { key: "ArrowRight" });
    const after = svg.getAttribute("viewBox");
    expect(after).not.toBe(before);
    expect(Number(after?.split(" ")[0])).toBeGreaterThan(
      Number(before?.split(" ")[0]),
    );
    fireEvent.keyDown(map, { key: "-" });
    expect(map).toHaveAttribute("data-zoom", "1.50");
    fireEvent.keyDown(map, { key: "0" });
    expect(map).toHaveAttribute("data-zoom", "1.00");
    expect(svg.getAttribute("viewBox")).toBe("0.00 0.00 1000.00 520.00");
    // Modified keys belong to the browser.
    fireEvent.keyDown(map, { key: "+", ctrlKey: true });
    expect(map).toHaveAttribute("data-zoom", "1.00");
  });

  it("zooms with the buttons, the wheel and the regions", async () => {
    fakePlaces();
    const user = userEvent.setup();
    await openMap();
    const map = mapGroup();
    const zoomOut = screen.getByRole("button", { name: "Zoom out" });
    const reset = screen.getByRole("button", { name: "Show the whole world" });
    expect(zoomOut).toBeDisabled();
    expect(reset).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(map).toHaveAttribute("data-zoom", "1.50");
    expect(zoomOut).toBeEnabled();
    await user.click(zoomOut);
    expect(map).toHaveAttribute("data-zoom", "1.00");

    fireEvent.wheel(map, { deltaY: -400 });
    expect(Number(map.getAttribute("data-zoom"))).toBeGreaterThan(1);
    await user.click(reset);
    expect(map).toHaveAttribute("data-zoom", "1.00");

    const regions = screen.getByRole("group", { name: "Region" });
    expect(
      within(regions).getByRole("button", { name: "World" }),
    ).toHaveAttribute("aria-pressed", "true");
    await user.click(within(regions).getByRole("button", { name: "Europe" }));
    expect(
      within(regions).getByRole("button", { name: "Europe" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(Number(map.getAttribute("data-zoom"))).toBeGreaterThan(3);
    // Europe hides the United States pin; Paris and London stay.
    expect(
      within(map).queryByRole("button", { name: /^United States:/ }),
    ).toBeNull();
    expect(within(map).getByRole("button", { name: /^Paris:/ })).toBeVisible();
  });

  it("drags to pan once zoomed", async () => {
    fakePlaces();
    await openMap();
    const map = mapGroup();
    map.getBoundingClientRect = () =>
      ({
        left: 0,
        top: 0,
        width: 1000,
        height: 520,
        right: 1000,
        bottom: 520,
      }) as DOMRect;
    fireEvent.keyDown(map, { key: "+" });
    const svg = screen.getByTestId("world-svg");
    const before = Number(svg.getAttribute("viewBox")?.split(" ")[0]);
    fireEvent.pointerDown(map, { pointerId: 1, clientX: 500, clientY: 260 });
    fireEvent.pointerMove(map, { pointerId: 1, clientX: 400, clientY: 260 });
    fireEvent.pointerUp(map, { pointerId: 1 });
    const after = Number(svg.getAttribute("viewBox")?.split(" ")[0]);
    expect(after).toBeGreaterThan(before);
    // Moves after the pointer is up do nothing.
    fireEvent.pointerMove(map, { pointerId: 1, clientX: 0, clientY: 0 });
    expect(Number(svg.getAttribute("viewBox")?.split(" ")[0])).toBe(after);
  });

  it("sends the URL filters to the api, without the sort", async () => {
    const requests = fakePlaces();
    await openMap("/jobs/map?status=applied&type=VIE&fit_min=60&sort=fit");
    const last = requests.at(-1);
    expect(last?.getAll("status")).toEqual(["applied"]);
    expect(last?.getAll("type")).toEqual(["VIE"]);
    expect(last?.get("fit_min")).toBe("60");
    expect(last?.has("sort")).toBe(false);
  });

  it("refetches when a filter changes and keeps the URL in step", async () => {
    const requests = fakePlaces();
    const user = userEvent.setup();
    const app = await openMap("/jobs/map?status=found");
    await user.click(screen.getByRole("button", { name: /^Status/ }));
    await user.click(
      await screen.findByRole("menuitemcheckbox", { name: "Applied" }),
    );
    await waitFor(() => {
      expect(app.location()).toBe("/jobs/map?status=found&status=applied");
    });
    await waitFor(() => {
      expect(requests.at(-1)?.getAll("status")).toEqual(["found", "applied"]);
    });
  });

  it("shows an error with Retry when the api fails", async () => {
    let fail = true;
    fakePlaces(() =>
      fail
        ? HttpResponse.json({ detail: "Boom." }, { status: 500 })
        : HttpResponse.json(MAP),
    );
    const user = userEvent.setup();
    renderApp("/jobs/map");
    expect(
      await screen.findByText(/The map could not be loaded/),
    ).toBeVisible();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(
      await screen.findByRole("list", { name: "Places, most postings first" }),
    ).toBeVisible();
  });
});

describe("view tabs", () => {
  it("switch between list and map, keeping the filters", async () => {
    fakeJobsApi([makeJob(1)]);
    fakePlaces();
    const user = userEvent.setup();
    const app = renderApp("/jobs?status=found");
    const views = await screen.findByRole("navigation", { name: "Jobs views" });
    expect(within(views).getByRole("link", { name: "List" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await user.click(within(views).getByRole("link", { name: "Map" }));
    await screen.findByRole("heading", { level: 1, name: "Jobs map" });
    expect(app.location()).toBe("/jobs/map?status=found");
    const mapViews = screen.getByRole("navigation", { name: "Jobs views" });
    expect(within(mapViews).getByRole("link", { name: "Map" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await user.click(within(mapViews).getByRole("link", { name: "List" }));
    await screen.findByRole("heading", { level: 1, name: "Jobs" });
    expect(app.location()).toBe("/jobs?status=found");
  });
});
