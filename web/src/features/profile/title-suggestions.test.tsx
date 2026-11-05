import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp, renderWithQuery } from "@/test/render";
import { defaultApplySettings, server } from "@/test/server";

import { EMPTY, profileKey, type SectionData } from "./api";
import type { TitleSuggestions as Data } from "./title-suggestions-api";
import { PlaceSuggestion, TitleSuggestions } from "./title-suggestions";

const URL = "*/api/profile/title-suggestions";

const IDEAS = [
  {
    title: "Analytics engineer",
    reason: "Your SQL and dbt work matches this role.",
    language: "en",
  },
  {
    title: "Ingénieur données",
    reason: "Vos pipelines en Python et SQL.",
    language: "fr",
  },
];

function answer(patch: Partial<Data> = {}): Data {
  return {
    status: "idle",
    needs: null,
    suggestions: IDEAS,
    refreshed_at: "2026-11-05T09:00:00+00:00",
    error: null,
    asks_left: 3,
    asks_per_day: 3,
    estimate: {
      tokens: { low: 19844, high: 38964 },
      cost: { currency: "USD", low: "0.03", high: "0.09" },
    },
    cost_note: "Estimates from list prices.",
    ...patch,
  };
}

/** GET answers `first`, then whatever `next` holds; POST answers `post` and counts. */
function fakeIdeas(first: Data, post?: () => Response) {
  const calls = { gets: 0, posts: 0 };
  let current = first;
  server.use(
    http.get(URL, () => {
      calls.gets += 1;
      return HttpResponse.json(current);
    }),
    http.post(URL, () => {
      calls.posts += 1;
      return post ? post() : HttpResponse.json(current);
    }),
  );
  return {
    calls,
    set: (next: Data) => {
      current = next;
    },
  };
}

function Harness({
  initial = [],
  quiet = false,
}: {
  initial?: string[];
  quiet?: boolean;
}) {
  const [titles, setTitles] = useState(initial);
  return (
    <MemoryRouter>
      <TitleSuggestions
        current={titles}
        label="Job titles"
        quiet={quiet}
        onAdd={(title) => {
          setTitles((list) => [...list, title]);
        }}
      />
      <ul aria-label="The list">
        {titles.map((title) => (
          <li key={title}>{title}</li>
        ))}
      </ul>
    </MemoryRouter>
  );
}

const heading = () =>
  screen.findByRole("heading", { name: "Suggested for you" });

describe("TitleSuggestions: the states", () => {
  it("without a model key: the one link that makes ideas possible", async () => {
    fakeIdeas(answer({ needs: "model_key", suggestions: [], estimate: null }));
    renderWithQuery(<Harness />);
    await heading();
    expect(
      screen.getByText(/Job title ideas from your profile need a model key/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Add a model key" }),
    ).toHaveAttribute("href", "/account");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("without a CV or skills: a link to add the CV", async () => {
    fakeIdeas(answer({ needs: "cv", suggestions: [] }));
    renderWithQuery(<Harness />);
    await heading();
    expect(screen.getByRole("link", { name: "Add your CV" })).toHaveAttribute(
      "href",
      "/profile/fill",
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("first ideas: the cost before the click, then on its way, then the ideas", async () => {
    const fake = fakeIdeas(
      answer({ suggestions: [], refreshed_at: null }),
      () =>
        HttpResponse.json(
          answer({ suggestions: [], refreshed_at: null, status: "running" }),
        ),
    );
    const user = userEvent.setup();
    const { client } = renderWithQuery(<Harness />);
    await screen.findByText("Get job title ideas that fit your profile.");
    expect(
      screen.getByText("About USD 0.03 to USD 0.09 each time, at list prices."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Suggest titles" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Finding titles that fit your profile",
    );
    expect(fake.calls.posts).toBe(1);
    expect(
      screen.queryByRole("button", { name: /Suggest titles|More ideas/ }),
    ).toBeNull();
    fake.set(answer());
    await client.invalidateQueries();
    expect(
      await screen.findByRole("button", {
        name: "Add Analytics engineer to Job titles",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("each idea reads + title with its reason; one click adds it and it leaves", async () => {
    fakeIdeas(answer());
    const user = userEvent.setup();
    renderWithQuery(<Harness initial={["Data engineer"]} />);
    const add = await screen.findByRole("button", {
      name: "Add Analytics engineer to Job titles",
    });
    expect(add).toHaveAccessibleDescription(
      "Your SQL and dbt work matches this role.",
    );
    const french = screen.getByRole("button", {
      name: "Add Ingénieur données to Job titles",
    });
    expect(within(french).getByText("Ingénieur données")).toHaveAttribute(
      "lang",
      "fr",
    );
    await user.click(add);
    expect(
      within(screen.getByRole("list", { name: "The list" })).getByText(
        "Analytics engineer",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Add Analytics engineer to Job titles",
      }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "More ideas" }),
    ).toBeInTheDocument();
  });

  it("an idea already in the list, in another case or with accents aside, is not shown", async () => {
    fakeIdeas(answer());
    renderWithQuery(
      <Harness initial={["analytics engineers", "Ingenieur donnees"]} />,
    );
    await heading();
    await screen.findByText("No new title to suggest.");
    expect(screen.queryByRole("button", { name: /^Add / })).toBeNull();
  });

  it("failed: the plain reason and Try again", async () => {
    const fake = fakeIdeas(
      answer({
        suggestions: [],
        status: "failed",
        error: "The suggestions could not be made. Try again in a minute.",
      }),
    );
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "The suggestions could not be made. Try again in a minute.",
    );
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(fake.calls.posts).toBe(1);
    });
  });

  it("nothing new after the checks: says so, and More ideas stays", async () => {
    fakeIdeas(answer({ suggestions: [] }));
    renderWithQuery(<Harness />);
    expect(
      await screen.findByText("No new title to suggest."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "More ideas" }),
    ).toBeInTheDocument();
  });

  it("the day's clicks used up: no More ideas, and it says when", async () => {
    fakeIdeas(answer({ asks_left: 0 }));
    renderWithQuery(<Harness />);
    expect(
      await screen.findByText(
        "You asked for more ideas 3 times today. More tomorrow.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More ideas" })).toBeNull();
    expect(screen.queryByText(/USD/)).toBeNull();
  });

  it("on a subscription nothing is said about money", async () => {
    fakeIdeas(
      answer({
        estimate: { tokens: { low: 19844, high: 38964 }, cost: null },
        cost_note: null,
      }),
    );
    renderWithQuery(<Harness />);
    await screen.findByRole("button", { name: "More ideas" });
    expect(screen.queryByText(/USD|\$|cost|price/i)).toBeNull();
  });

  it("a click the server refuses says why", async () => {
    fakeIdeas(answer(), () =>
      HttpResponse.json(
        {
          detail: "You asked for more ideas 3 times today. Try again tomorrow.",
        },
        { status: 429 },
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await user.click(await screen.findByRole("button", { name: "More ideas" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      /3 times today/,
    );
  });

  it("quiet (the wizard): nothing at all until a key and something to read exist", async () => {
    const fake = fakeIdeas(answer({ needs: "model_key", suggestions: [] }));
    renderWithQuery(<Harness quiet />);
    await waitFor(() => {
      expect(fake.calls.gets).toBe(1);
    });
    expect(
      screen.queryByRole("heading", { name: "Suggested for you" }),
    ).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("PlaceSuggestion", () => {
  function seeded(
    identity: Partial<SectionData["identity"]>,
    current: string[],
  ) {
    const added: string[] = [];
    const view = renderWithQuery(
      <PlaceSuggestion
        current={current}
        label="Places"
        onAdd={(place) => added.push(place)}
      />,
    );
    view.client.setQueryData(profileKey, {
      sections: { ...EMPTY, identity: { ...EMPTY.identity, ...identity } },
    });
    return { added, view };
  }

  it("offers the profile's city, and one click adds it", async () => {
    const { added } = seeded({ city: "Lyon", country: "France" }, []);
    const user = userEvent.setup();
    const chip = await screen.findByRole("button", {
      name: "Add Lyon, from your profile, to Places",
    });
    expect(chip).toHaveTextContent("Lyon, from your profile");
    await user.click(chip);
    expect(added).toEqual(["Lyon"]);
  });

  it("offers the country when no city is set, and nothing when the list holds it", async () => {
    seeded({ city: null, country: "France" }, ["Remote"]);
    expect(
      await screen.findByRole("button", {
        name: "Add France, from your profile, to Places",
      }),
    ).toBeInTheDocument();
  });

  it("says nothing when the list already holds the place", () => {
    seeded({ city: "Lyon" }, ["lyon"]);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("never asks for the profile itself", () => {
    // No handler for GET /api/profile: a request would fail the test.
    renderWithQuery(
      <PlaceSuggestion current={[]} label="Places" onAdd={() => {}} />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("in the profile's Search tab", () => {
  it("adds an idea and the place to the lists, and Save sends them", async () => {
    fakeSession(admin);
    const sections = haraldLike();
    sections.identity = { ...sections.identity, city: "Lyon" };
    sections.search = {
      ...sections.search,
      title_include: { advanced_regex: false, terms: ["llm"] },
      location: { advanced_regex: false, terms: [] },
    };
    const state = fakeProfile(sections);
    fakeIdeas(answer());
    const user = userEvent.setup();
    renderApp("/profile?tab=search");
    await screen.findByRole("form", { name: "Search" });
    await user.click(
      await screen.findByRole("button", {
        name: "Add Analytics engineer to Job titles",
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name: "Add Lyon, from your profile, to Places",
      }),
    );
    expect(
      within(
        screen.getByRole("list", { name: "Terms of Job titles" }),
      ).getByText("Analytics engineer"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "Terms of Places" })).getByText(
        "Lyon",
      ),
    ).toBeInTheDocument();
    expect(state.saves).toEqual([]); // the save stays the person's
    await user.click(screen.getByRole("button", { name: "Save search" }));
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    expect(state.saves[0]?.body.data).toMatchObject({
      title_include: { terms: ["llm", "Analytics engineer"] },
      location: { terms: ["Lyon"] },
    });
  });

  it("a list of regular expressions gets no idea", async () => {
    fakeSession(admin);
    const sections = haraldLike();
    sections.search = {
      ...sections.search,
      title_include: { advanced_regex: true, terms: ["\\bllm\\b"] },
    };
    fakeProfile(sections);
    fakeIdeas(answer());
    renderApp("/profile?tab=search");
    await screen.findByRole("form", { name: "Search" });
    expect(screen.queryByRole("button", { name: /^Add Analytics/ })).toBeNull();
  });
});

describe("in the wizard", () => {
  function wizard(sections: Partial<SectionData>, done: string[] = []) {
    fakeSession(member);
    fakeProfile(sections);
    server.use(
      http.get("*/api/opportunities/onboarding", () =>
        HttpResponse.json({
          completed_at: null,
          dismissed_at: null,
          profile_empty: false,
          done,
          passed: ["profile"],
          steps_left: ["looking", "where", "work"],
          open_wizard: true,
          show_card: true,
        }),
      ),
      http.get("*/api/apply/settings", () =>
        HttpResponse.json(defaultApplySettings),
      ),
    );
  }

  it("What you are looking for: an idea goes into the roles", async () => {
    wizard({ search: { ...EMPTY.search } });
    fakeIdeas(answer());
    const user = userEvent.setup();
    renderApp("/welcome?step=looking");
    const roles = await screen.findByLabelText("Roles");
    await user.type(roles, "Data engineer");
    await user.click(
      await screen.findByRole("button", {
        name: "Add Analytics engineer to Roles",
      }),
    );
    expect(roles).toHaveValue("Data engineer, Analytics engineer");
    expect(
      screen.queryByRole("button", { name: "Add Analytics engineer to Roles" }),
    ).toBeNull();
  });

  it("What you are looking for: says nothing before the key and the CV", async () => {
    wizard({ search: { ...EMPTY.search } });
    const fake = fakeIdeas(answer({ needs: "model_key", suggestions: [] }));
    renderApp("/welcome?step=looking");
    await screen.findByLabelText("Roles");
    await waitFor(() => {
      expect(fake.calls.gets).toBeGreaterThan(0);
    });
    expect(screen.queryByText("Suggested for you")).toBeNull();
    expect(screen.queryByRole("link", { name: "Add a model key" })).toBeNull();
  });

  it("Where: the place of the profile goes into the places", async () => {
    wizard(
      {
        search: {
          ...EMPTY.search,
          title_include: { advanced_regex: false, terms: ["Data engineer"] },
        },
        identity: { ...EMPTY.identity, city: "Lyon" },
      },
      ["looking"],
    );
    const user = userEvent.setup();
    renderApp("/welcome?step=where");
    const places = await screen.findByLabelText("Places");
    await user.click(
      await screen.findByRole("button", {
        name: "Add Lyon, from your profile, to Places",
      }),
    );
    expect(places).toHaveValue("Lyon");
  });
});
