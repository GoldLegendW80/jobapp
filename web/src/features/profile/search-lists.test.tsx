import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useForm, type UseFormReturn } from "react-hook-form";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { WORDING } from "@/lib/wording";
import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";

import { EMPTY, type Search, type SectionData } from "./api";
import { toSearchForm, type SearchForm } from "./editor-forms";
import { SearchLists } from "./search-lists";

afterEach(() => {
  toast.dismiss();
});

const LABELS = [
  WORDING.jobTitles,
  WORDING.unwantedTitles,
  WORDING.places,
  WORDING.unwantedPlaces,
];

/** Harald's profile with the four lists as plain words. */
function plain(): SectionData {
  const sections = haraldLike();
  sections.search = {
    ...sections.search,
    title_include: { advanced_regex: false, terms: ["applied ai", "llm"] },
    title_exclude: { advanced_regex: false, terms: ["cyber"] },
  };
  return sections;
}

async function openSearch(initial: SectionData) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const user = userEvent.setup();
  renderApp("/profile?tab=search");
  await screen.findByRole("form", { name: "Search" });
  return { user, state };
}

const lists = () =>
  within(
    screen
      .getByRole("heading", { name: "Titles and places" })
      .closest("section")!,
  );
const save = () => screen.getByRole("button", { name: "Save search" });
const saved = (state: ReturnType<typeof fakeProfile>) =>
  state.saves.at(-1)!.body.data as Search;
const noSwitch = () => {
  // Words only (owner, 2026-10-28): no switch, folded or not, and no Advanced for the lists.
  expect(
    screen.queryByRole("switch", { name: /regular expressions/i }),
  ).toBeNull();
  expect(lists().queryByRole("button", { name: "Advanced" })).toBeNull();
};

describe("the four plain lists of the search", () => {
  it("shows the lists as words, with no regular expression switch", async () => {
    await openSearch(plain());
    for (const label of LABELS) {
      expect(lists().getByRole("heading", { name: label })).toBeVisible();
      expect(screen.getByLabelText(`New term for ${label}`)).toBeVisible();
    }
    expect(
      screen.getByRole("list", { name: "Terms of Job titles" }),
    ).toHaveTextContent("applied ai");
    expect(
      screen.getByRole("list", { name: "Terms of Unwanted places" }),
    ).toHaveTextContent("United States");
    noSwitch();
    expect(screen.queryByText(WORDING.listUsesRegularExpressions)).toBeNull();
  });

  it("keeps a list saved as regular expressions working, says so, and saves it as it was", async () => {
    const { user, state } = await openSearch(haraldLike());
    noSwitch();
    expect(
      screen.getAllByText(WORDING.listUsesRegularExpressions),
    ).toHaveLength(2);
    expect(WORDING.listUsesRegularExpressions).not.toMatch(/[–—]| - /);
    await user.type(
      screen.getByLabelText("New term for Unwanted titles"),
      "intern{enter}",
    );
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).title_include).toEqual({
      advanced_regex: true,
      terms: ["applied ai", "llm"],
    });
    expect(saved(state).title_exclude).toEqual({
      advanced_regex: true,
      terms: ["\\bcyber\\b", "intern"],
    });
  });

  it("saves what the person types in a plain list as words", async () => {
    const { user, state } = await openSearch(plain());
    await user.type(
      screen.getByLabelText("New term for Unwanted places"),
      "Remote (US){enter}",
    );
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).location_exclude).toEqual({
      advanced_regex: false,
      terms: ["United States", "Remote (US)"],
    });
    expect(saved(state).title_include.advanced_regex).toBe(false);
  });

  it("reads a search saved before the unwanted places as an empty list", async () => {
    const sections = plain();
    const old: Search = { ...sections.search };
    delete old.location_exclude;
    const { user, state } = await openSearch({ ...sections, search: old });
    const unwanted = lists()
      .getByRole("heading", { name: WORDING.unwantedPlaces })
      .closest("div")!.parentElement!;
    expect(within(unwanted).getByText("No term yet.")).toBeVisible();
    await user.type(
      screen.getByLabelText("New term for Unwanted places"),
      "Canada{enter}",
    );
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).location_exclude).toEqual({
      advanced_regex: false,
      terms: ["Canada"],
    });
    expect(saved(state).location).toEqual(sections.search.location);
  });
});

describe("a reload of the lists", () => {
  it("shows the note of a list of regular expressions it brings, with no switch", () => {
    let form: UseFormReturn<SearchForm> | undefined;
    function Harness() {
      form = useForm<SearchForm>({
        defaultValues: toSearchForm(structuredClone(EMPTY.search)),
      });
      return <SearchLists form={form} />;
    }
    render(<Harness />);
    expect(screen.queryByText(WORDING.listUsesRegularExpressions)).toBeNull();
    act(() => {
      form!.reset(
        toSearchForm({
          ...structuredClone(EMPTY.search),
          location_exclude: { advanced_regex: true, terms: ["\\bUSA?\\b"] },
        }),
      );
    });
    expect(screen.getByText(WORDING.listUsesRegularExpressions)).toBeVisible();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("button", { name: "Advanced" })).toBeNull();
  });
});
