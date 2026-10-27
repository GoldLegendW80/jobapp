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
const advanced = () => lists().getByRole("button", { name: "Advanced" });
const regexSwitch = (label: string) =>
  screen.getByRole("switch", { name: `Regular expressions for ${label}` });
const save = () => screen.getByRole("button", { name: "Save search" });
const saved = (state: ReturnType<typeof fakeProfile>) =>
  state.saves.at(-1)!.body.data as Search;

describe("the four plain lists of the search", () => {
  it("shows the lists as words, with their switches folded under Advanced", async () => {
    const { user } = await openSearch(plain());
    for (const label of LABELS) {
      expect(lists().getByRole("heading", { name: label })).toBeVisible();
      expect(screen.getByLabelText(`New term for ${label}`)).toBeVisible();
      expect(
        screen.queryByRole("switch", {
          name: `Advanced (regular expressions) for ${label}`,
        }),
      ).toBeNull();
    }
    expect(
      screen.getByRole("list", { name: "Terms of Job titles" }),
    ).toHaveTextContent("applied ai");
    expect(
      screen.getByRole("list", { name: "Terms of Unwanted places" }),
    ).toHaveTextContent("United States");
    expect(advanced()).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("switch", { name: /^Regular expressions for/ }),
    ).toBeNull();
    expect(screen.queryByText(WORDING.listUsesRegularExpressions)).toBeNull();

    await user.click(advanced());
    for (const label of LABELS) expect(regexSwitch(label)).not.toBeChecked();
  });

  it("opens Advanced by itself for a saved regular expression and keeps it working", async () => {
    const { user, state } = await openSearch(haraldLike());
    expect(advanced()).toHaveAttribute("aria-expanded", "true");
    expect(regexSwitch(WORDING.jobTitles)).toBeChecked();
    expect(regexSwitch(WORDING.unwantedTitles)).toBeChecked();
    expect(regexSwitch(WORDING.places)).not.toBeChecked();
    expect(regexSwitch(WORDING.unwantedPlaces)).not.toBeChecked();
    // Folded, the lists still say they hold regular expressions.
    await user.click(advanced());
    expect(advanced()).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getAllByText(WORDING.listUsesRegularExpressions),
    ).toHaveLength(2);
    await user.type(
      screen.getByLabelText("New term for Unwanted titles"),
      "\\bintern{enter}",
    );
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).title_include).toEqual({
      advanced_regex: true,
      terms: ["applied ai", "llm"],
    });
    expect(saved(state).title_exclude).toEqual({
      advanced_regex: true,
      terms: ["\\bcyber\\b", "\\bintern"],
    });
  });

  it("turns a list to regular expressions under Advanced, checks its terms that way, and saves it", async () => {
    const { user, state } = await openSearch(plain());
    await user.click(advanced());
    const input = screen.getByLabelText("New term for Unwanted places");
    await user.click(regexSwitch(WORDING.unwantedPlaces));
    expect(regexSwitch(WORDING.unwantedPlaces)).toBeChecked();
    expect(
      screen.getAllByText(WORDING.listUsesRegularExpressions),
    ).toHaveLength(1);
    await user.type(input, "(us{enter}");
    expect(await screen.findByText(/Invalid regular expression/)).toBeVisible();
    // Back to plain words: what was said about the draft no longer holds, and it is a word.
    await user.click(regexSwitch(WORDING.unwantedPlaces));
    expect(screen.queryByText(/Invalid regular expression/)).toBeNull();
    await user.click(regexSwitch(WORDING.unwantedPlaces));
    await user.clear(input);
    await user.type(input, "\\bUSA?\\b{enter}");
    await user.click(save());
    await screen.findByText("Search saved.");
    expect(saved(state).location_exclude).toEqual({
      advanced_regex: true,
      terms: ["United States", "\\bUSA?\\b"],
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

describe("the lists' Advanced", () => {
  it("opens again when a reload brings a list of regular expressions", async () => {
    let form: UseFormReturn<SearchForm> | undefined;
    function Harness() {
      form = useForm<SearchForm>({
        defaultValues: toSearchForm(structuredClone(EMPTY.search)),
      });
      return <SearchLists form={form} />;
    }
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Advanced" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    act(() => {
      form!.reset(
        toSearchForm({
          ...structuredClone(EMPTY.search),
          location_exclude: { advanced_regex: true, terms: ["\\bUSA?\\b"] },
        }),
      );
    });
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(regexSwitch(WORDING.unwantedPlaces)).toBeChecked();
    // Folded by the person, it stays folded while the list keeps its expressions.
    await userEvent.setup().click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText(WORDING.listUsesRegularExpressions)).toBeVisible();
  });
});
