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
    note: null,
    messages: [],
    message_max: 300,
    asks_left: 3,
    asks_per_day: 3,
    estimate: {
      tokens: { low: 18816, high: 29424 },
      cost: { currency: "USD", low: "0.03", high: "0.07" },
    },
    cost_note: "Estimates from list prices.",
    ...patch,
  };
}

/** GET answers `first`, then whatever `next` holds; POST answers `post` and counts, with its
 * bodies; /dismiss and /messages act on the current answer as the server does. */
function fakeIdeas(
  first: Data,
  post?: (body: unknown) => Response,
  dismissed?: () => Response,
) {
  const calls = {
    gets: 0,
    posts: 0,
    bodies: [] as unknown[],
    dismissed: [] as string[],
    clears: 0,
  };
  let current = first;
  server.use(
    http.get(URL, () => {
      calls.gets += 1;
      return HttpResponse.json(current);
    }),
    http.post(URL, async ({ request }) => {
      calls.posts += 1;
      const body: unknown = await request.json();
      calls.bodies.push(body);
      return post ? post(body) : HttpResponse.json(current);
    }),
    http.post(`${URL}/dismiss`, async ({ request }) => {
      const { title } = (await request.json()) as { title: string };
      calls.dismissed.push(title);
      if (dismissed) return dismissed();
      current = {
        ...current,
        suggestions: current.suggestions.filter((one) => one.title !== title),
      };
      return HttpResponse.json(current);
    }),
    http.delete(`${URL}/messages`, () => {
      calls.clears += 1;
      current = { ...current, messages: [] };
      return HttpResponse.json(current);
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

  it("with nothing to read: what is missing in plain words, and a message alone starts the ideas", async () => {
    const fake = fakeIdeas(
      answer({ needs: "input", suggestions: [], refreshed_at: null }),
      () =>
        HttpResponse.json(
          answer({
            suggestions: [],
            refreshed_at: null,
            status: "running",
            messages: [
              { text: "Move into product", at: "2026-11-05T09:00:00Z" },
            ],
          }),
        ),
    );
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await heading();
    expect(
      screen.getByText(
        /Add your CV, fill your profile or tell the assistant what you want to do/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add your CV" })).toHaveAttribute(
      "href",
      "/profile/fill",
    );
    expect(
      screen.queryByRole("button", { name: /More ideas|Suggest/ }),
    ).toBeNull();
    const box = screen.getByLabelText("Tell the assistant what you want to do");
    expect(box).toHaveAttribute(
      "placeholder",
      "For example: move into product, stay in data, work remotely",
    );
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await user.type(box, "  Move into product ");
    await user.click(send);
    await waitFor(() => {
      expect(fake.calls.bodies).toEqual([{ message: "Move into product" }]);
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Finding titles that fit your profile",
    );
    expect(box).toHaveValue("");
    expect(
      screen.getByText("You told the assistant: “Move into product”"),
    ).toBeInTheDocument();
  });

  it("in a dialog, nothing to read is said without a link", async () => {
    fakeIdeas(answer({ needs: "input", suggestions: [] }));
    renderWithQuery(
      <MemoryRouter>
        <TitleSuggestions
          current={[]}
          label="Job titles"
          links={false}
          onAdd={() => {}}
        />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText(
        "Job title ideas show here once you add your CV, fill your profile or tell the assistant what you want to do.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
    expect(
      screen.getByLabelText("Tell the assistant what you want to do"),
    ).toBeEnabled();
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
      screen.getByText("About USD 0.03 to USD 0.07 each time, at list prices."),
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
    // A message is one ask too: the box waits for tomorrow.
    expect(
      screen.getByLabelText("Tell the assistant what you want to do"),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
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

describe("TitleSuggestions: the chat and Not for me", () => {
  it("inside the page's form, Enter sends the message and never submits the form", async () => {
    const fake = fakeIdeas(answer(), () =>
      HttpResponse.json(answer({ status: "running" })),
    );
    const submits: number[] = [];
    const user = userEvent.setup();
    renderWithQuery(
      <form
        aria-label="Search"
        onSubmit={(event) => {
          event.preventDefault();
          submits.push(1);
        }}
      >
        <Harness />
      </form>,
    );
    const box = await screen.findByLabelText(
      "Tell the assistant what you want to do",
    );
    await user.type(box, "{Enter}");
    expect(fake.calls.posts).toBe(0); // nothing typed: nothing sent
    await user.type(box, "Stay in data, work remotely{Enter}");
    await waitFor(() => {
      expect(fake.calls.bodies).toEqual([
        { message: "Stay in data, work remotely" },
      ]);
    });
    expect(submits).toEqual([]);
  });

  it("More ideas sends no message, and the new ideas show after the ones there", async () => {
    const fake = fakeIdeas(answer(), () =>
      HttpResponse.json(answer({ status: "running" })),
    );
    const user = userEvent.setup();
    const { client } = renderWithQuery(<Harness />);
    await user.click(await screen.findByRole("button", { name: "More ideas" }));
    await waitFor(() => {
      expect(fake.calls.bodies).toEqual([{}]);
    });
    // Running: the ideas shown stay, the box waits.
    expect(
      screen.getByRole("button", {
        name: "Add Analytics engineer to Job titles",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Tell the assistant what you want to do"),
    ).toBeDisabled();
    fake.set(
      answer({
        suggestions: [
          ...IDEAS,
          { title: "BI developer", reason: "Your dashboards.", language: "en" },
        ],
      }),
    );
    await client.invalidateQueries();
    await screen.findByRole("button", {
      name: "Add BI developer to Job titles",
    });
    expect(
      screen
        .getAllByRole("button", { name: /^Add / })
        .map((one) => one.getAttribute("aria-label")),
    ).toEqual([
      "Add Analytics engineer to Job titles",
      "Add Ingénieur données to Job titles",
      "Add BI developer to Job titles",
    ]);
  });

  it("a run that added nothing says so, and the ideas stay", async () => {
    fakeIdeas(
      answer({ note: "No new idea this time. The ideas you had stay." }),
    );
    renderWithQuery(<Harness />);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "No new idea this time. The ideas you had stay.",
    );
    expect(
      screen.getByRole("button", {
        name: "Add Analytics engineer to Job titles",
      }),
    ).toBeInTheDocument();
  });

  it("shows the last message with the earlier ones counted, and Clear forgets them", async () => {
    const fake = fakeIdeas(
      answer({
        messages: [
          { text: "Stay in data", at: "2026-11-04T09:00:00Z" },
          { text: "Move into product", at: "2026-11-05T09:00:00Z" },
        ],
      }),
    );
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    expect(
      await screen.findByText(
        "You told the assistant: “Move into product” It also reads your 1 earlier message.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Stay in data/)).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Clear what you told the assistant" }),
    );
    await waitFor(() => {
      expect(fake.calls.clears).toBe(1);
    });
    await waitFor(() => {
      expect(screen.queryByText(/You told the assistant/)).toBeNull();
    });
    expect(fake.calls.posts).toBe(0);
  });

  it("Not for me removes an idea at once and asks the server to keep it away", async () => {
    const fake = fakeIdeas(answer());
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    const dismiss = await screen.findByRole("button", {
      name: "Not for me: Analytics engineer",
    });
    await user.hover(dismiss);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Not for me");
    await user.click(dismiss);
    expect(
      screen.queryByRole("button", {
        name: "Add Analytics engineer to Job titles",
      }),
    ).toBeNull();
    await waitFor(() => {
      expect(fake.calls.dismissed).toEqual(["Analytics engineer"]);
    });
    expect(
      screen.getByRole("button", {
        name: "Add Ingénieur données to Job titles",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("list", { name: "The list" }),
    ).toBeEmptyDOMElement();
  });

  it("Not for me that fails brings the idea back and says why", async () => {
    fakeIdeas(answer(), undefined, () =>
      HttpResponse.json(
        { detail: "Too many clicks. Try again in a minute." },
        { status: 429 },
      ),
    );
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await user.click(
      await screen.findByRole("button", {
        name: "Not for me: Analytics engineer",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Too many clicks. Try again in a minute.",
    );
    expect(
      await screen.findByRole("button", {
        name: "Add Analytics engineer to Job titles",
      }),
    ).toBeInTheDocument();
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
    renderApp("/settings/search");
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
    renderApp("/settings/search");
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
