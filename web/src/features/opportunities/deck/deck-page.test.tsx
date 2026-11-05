import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeSession, member } from "@/test/auth";
import { fakeJobsApi, makeDetail, makeJob } from "@/test/jobs";
import { fakeProfile } from "@/test/profile";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { LIKED, SWIPE_TEXT } from "./card-text";
import { fakeDeck, makeCard, refused } from "./deck-test-api";
import { SWIPE_THRESHOLD } from "./swipe-card";

const NEVER =
  "This role matches one of your roles to never apply for, so it is never applied to.";
const SAME_ROLE =
  "An application to the same role at this company is already under way.";
const ALREADY = "You already applied to this posting.";

const card = () => screen.findByTestId("deck-card");
const title = async () =>
  within(await card()).getByRole("heading", { level: 2 }).textContent;
/** Waits until the card on top is the one named. */
const onTop = (name: string) =>
  waitFor(async () => {
    expect(await title()).toBe(name);
  });
/** A toast (an earlier test's may still be leaving). */
const toastSays = async (text: string) => {
  expect((await screen.findAllByText(text))[0]).toBeVisible();
};
const likeButton = () => screen.getByRole("button", { name: "Like" });
const passButton = () => screen.getByRole("button", { name: "Pass" });
const undoButton = () => screen.getByRole("button", { name: /Undo last/ });

/** A person with postings and skills: an empty deck then means "under the threshold". */
function withPostingsAndSkills() {
  fakeJobsApi([makeJob(1)]);
  fakeProfile();
}

async function open(
  cards = [makeCard(1), makeCard(2), makeCard(3)],
  options = {},
) {
  withPostingsAndSkills();
  const deck = fakeDeck(cards, options);
  const app = renderApp("/discover");
  await card();
  return { deck, app };
}

/** A precise pointer (a mouse) or a touch screen, as the media query `(pointer: fine)` says. */
function finePointer(on: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: on && query === "(pointer: fine)",
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

function reducedMotion(on: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: on && query.includes("reduce"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  toast.dismiss();
  window.localStorage.clear();
  fakeSession(member);
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

describe("the card", () => {
  it("shows the spec's fields in plain words", async () => {
    await open([
      makeCard(1, {
        contract_type: "Freelance",
        pay: "600 EUR a day",
        work_mode: "remote",
        location: "",
      }),
    ]);
    const shown = await card();
    expect(
      within(shown).getByRole("heading", { name: "Example Role 1" }),
    ).toBeVisible();
    expect(shown).toHaveTextContent("Example Robotics 1");
    expect(shown).toHaveTextContent("Remote");
    expect(shown).toHaveTextContent("Freelance mission");
    expect(shown).toHaveTextContent("600 EUR a day");
    expect(shown).toHaveTextContent(
      "Matches: Python, FastAPI. Gap: Kubernetes.",
    );
    expect(
      within(shown).getByRole("link", { name: /via Greenhouse/ }),
    ).toHaveAttribute("href", "https://boards.example.org/example/jobs/1");
    expect(within(shown).getByLabelText("Fit 89 out of 100")).toBeVisible();
    expect(screen.getByText("1 posting left")).toBeVisible();
    expect(
      screen.getByRole("article", {
        name: "Example Role 1 at Example Robotics 1, Remote, Freelance mission, 600 EUR a day, Fit 89",
      }),
    ).toHaveAccessibleDescription("Matches: Python, FastAPI. Gap: Kubernetes.");
    expect(passButton()).toHaveAttribute("aria-keyshortcuts", "ArrowLeft");
    expect(likeButton()).toHaveAttribute("aria-keyshortcuts", "ArrowRight");
  });

  it.each([
    ["review", "Like: Review first"],
    ["full_auto", "Like: Full auto"],
  ] as const)(
    "names the mode a Like runs in (%s) and links it to the Applications settings",
    async (mode, said) => {
      await open(undefined, { mode });
      const badge = screen.getByTestId("deck-mode");
      expect(badge).toHaveTextContent(new RegExp(`^${said}$`));
      expect(badge).toHaveAttribute("href", "/settings/applications");
    },
  );

  it("says Pay not given, muted, when the posting states no pay", async () => {
    await open([makeCard(1, { pay: null })]);
    const shown = await card();
    expect(within(shown).getByText("Pay not given")).toHaveClass(
      "text-muted-foreground",
    );
    expect(shown).toHaveAccessibleName(
      "Example Role 1 at Example Robotics 1, Lyon, France, Permanent, Pay not given, Fit 89",
    );
  });

  it("names Pass and Like under their round buttons, and a tap on the name acts too", async () => {
    const { deck } = await open();
    const controls = screen.getByTestId("deck-controls");
    for (const name of ["Pass", "Like"]) {
      const label = within(controls).getByText(name, { selector: "label" });
      expect(label).toBeVisible();
      expect(label).toHaveAttribute(
        "for",
        screen.getByRole("button", { name }).id,
      );
    }
    await userEvent.click(
      within(controls).getByText("Like", { selector: "label" }),
    );
    await onTop("Example Role 2");
    expect(deck.calls).toContain(`POST like ${makeCard(1).id}`);
  });

  it("offers the keys only to a precise pointer", async () => {
    finePointer(true);
    const first = await open();
    expect(screen.getByTestId("deck-keys")).toHaveTextContent(
      "Keys: left arrow to pass, right arrow to like, U to undo the last one.",
    );
    first.app.unmount();
    finePointer(false);
    await open();
    expect(screen.queryByTestId("deck-keys")).toBeNull();
    expect(passButton()).toBeVisible();
  });

  it("opens the posting's text under More", async () => {
    await open();
    server.use(
      http.get("*/api/jobs/:id", () =>
        HttpResponse.json(
          makeDetail({ posting_text: "We build example robots." }),
        ),
      ),
    );
    await userEvent.click(
      within(await card()).getByRole("button", { name: "More" }),
    );
    expect(await screen.findByTestId("deck-posting-text")).toHaveTextContent(
      "We build example robots.",
    );
  });
});

describe("a Like", () => {
  it.each([
    ["review", LIKED.review],
    ["full_auto", LIKED.full_auto],
  ] as const)(
    "in %s takes the card away with the mode's toast",
    async (mode, said) => {
      const { deck } = await open(undefined, { mode });
      await userEvent.click(likeButton());
      await toastSays(said);
      await onTop("Example Role 2");
      expect(deck.calls).toContain(`POST like ${makeCard(1).id}`);
    },
  );

  it.each([
    ["never_apply", NEVER],
    ["same_role_live", SAME_ROLE],
    ["already_applied", ALREADY],
  ])(
    "refused as %s keeps the card, says why and offers Pass",
    async (code, detail) => {
      const { deck } = await open();
      deck.likeAnswers.set(makeCard(1).id, refused(code, detail));
      await userEvent.click(likeButton());
      expect(
        await screen.findByText(new RegExp(detail.slice(0, 30))),
      ).toBeVisible();
      expect(screen.getByRole("alert")).toHaveTextContent(
        SWIPE_TEXT.passOffered,
      );
      await onTop("Example Role 1");
      expect(likeButton()).toBeDisabled();
      expect(passButton()).toBeEnabled();
      await userEvent.click(passButton());
      await onTop("Example Role 2");
      expect(deck.calls.filter((c) => c.startsWith("POST like"))).toHaveLength(
        1,
      );
    },
  );

  it("answered 404 takes the card away", async () => {
    const { deck } = await open();
    deck.likeAnswers.set(makeCard(1).id, {
      status: 404,
      body: { detail: "Posting not found." },
    });
    await userEvent.click(likeButton());
    await toastSays(SWIPE_TEXT.gone);
    await onTop("Example Role 2");
  });

  it("is undone through apply's cancel and the card comes back", async () => {
    const { deck } = await open();
    await userEvent.click(likeButton());
    await onTop("Example Role 2");
    await userEvent.click(undoButton());
    await onTop("Example Role 1");
    expect(deck.calls).toContain(
      "POST cancel 00000000-0000-4000-9000-0000000000a1",
    );
  });

  it.each(["submitting", "submitted", "submit_unknown"])(
    "already %s is too late to undo and sends no cancel",
    async (state) => {
      const { deck } = await open();
      deck.applicationState = state;
      await userEvent.click(likeButton());
      await onTop("Example Role 2");
      await userEvent.click(undoButton());
      await toastSays(SWIPE_TEXT.tooLate);
      expect(deck.calls.some((c) => c.startsWith("POST cancel"))).toBe(false);
      await onTop("Example Role 2");
      expect(undoButton()).toBeDisabled();
    },
  );
});

describe("a Pass", () => {
  it("takes the card away and Undo last puts it back", async () => {
    const { deck } = await open();
    await userEvent.click(passButton());
    await toastSays(SWIPE_TEXT.passed);
    await onTop("Example Role 2");
    expect(undoButton()).toBeEnabled();
    await userEvent.click(undoButton());
    await onTop("Example Role 1");
    expect(deck.calls).toContain(`DELETE pass ${makeCard(1).id}`);
    expect(undoButton()).toBeDisabled();
  });

  it("refused (changed) keeps the card with the api's sentence", async () => {
    const { deck } = await open();
    deck.passAnswers.set(
      makeCard(1).id,
      refused("in_progress", "An application of this posting is under way."),
    );
    await userEvent.click(passButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An application of this posting is under way.",
    );
    await onTop("Example Role 1");
  });

  it("of the last card leaves only its undo: no Like is ever sent for a passed card", async () => {
    finePointer(true);
    const { deck } = await open([makeCard(1)]);
    await userEvent.click(passButton());
    expect(
      await screen.findByText("Nothing new above your threshold of 70."),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Like" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Pass" })).toBeNull();
    expect(undoButton()).toBeEnabled();
    expect(screen.queryByTestId("deck-keys")).toBeNull();
    await userEvent.keyboard("{ArrowRight}");
    expect(deck.calls.some((c) => c.startsWith("POST like"))).toBe(false);
    await userEvent.click(undoButton());
    await onTop("Example Role 1");
    await userEvent.click(likeButton());
    await toastSays(LIKED.review);
    const id = makeCard(1).id;
    const order = deck.calls.filter((c) => c.includes(id));
    expect(order).toEqual([
      `POST pass ${id}`,
      `DELETE pass ${id}`,
      `POST like ${id}`,
    ]);
  });
});

describe("one card in flight", () => {
  it("ignores a second swipe until the first is answered", async () => {
    const { deck } = await open();
    let release = () => {};
    deck.likeGate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await userEvent.click(likeButton());
    expect(likeButton()).toBeDisabled();
    expect(passButton()).toBeDisabled();
    await userEvent.keyboard("{ArrowRight}{ArrowLeft}");
    expect(deck.calls.filter((c) => c.startsWith("POST"))).toHaveLength(1);
    release();
    await onTop("Example Role 2");
    expect(deck.calls.filter((c) => c.startsWith("POST"))).toEqual([
      `POST like ${makeCard(1).id}`,
    ]);
  });
});

describe("keyboard", () => {
  it("left passes, right likes, U undoes", async () => {
    const { deck } = await open();
    await userEvent.keyboard("{ArrowLeft}");
    await onTop("Example Role 2");
    await userEvent.keyboard("u");
    await onTop("Example Role 1");
    await userEvent.keyboard("{ArrowRight}");
    await onTop("Example Role 2");
    const id = makeCard(1).id;
    expect(
      deck.calls.filter((c) => c.startsWith("POST") || c.startsWith("DELETE")),
    ).toEqual([`POST pass ${id}`, `DELETE pass ${id}`, `POST like ${id}`]);
  });

  it("moves the focus to the new card's title and announces it", async () => {
    await open();
    const announce = screen.getByTestId("deck-announce");
    expect(announce).toHaveAttribute("aria-live", "polite");
    expect(announce).toHaveTextContent("");
    // Nothing takes the focus before a swipe.
    expect(document.activeElement).toBe(document.body);
    await userEvent.keyboard("{ArrowLeft}");
    await onTop("Example Role 2");
    const heading = within(await card()).getByRole("heading", { level: 2 });
    await waitFor(() => {
      expect(heading).toHaveFocus();
    });
    expect(announce).toHaveTextContent(
      "Now showing Example Role 2 at Example Robotics 2.",
    );
  });

  it("does nothing while the focus is in a filter field", async () => {
    const { deck } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
    screen.getByLabelText("Source").focus();
    fireEvent.keyDown(screen.getByLabelText("Source"), { key: "ArrowRight" });
    expect(deck.calls.some((c) => c.startsWith("POST"))).toBe(false);
  });
});

describe("drag", () => {
  const drag = (to: number) => {
    const target = screen.getByTestId("deck-card");
    fireEvent.pointerDown(target, { pointerId: 1, button: 0, clientX: 200 });
    fireEvent.pointerMove(target, { pointerId: 1, clientX: 200 + to });
    fireEvent.pointerUp(target, { pointerId: 1, clientX: 200 + to });
  };

  it("past the threshold to the right is a Like, to the left a Pass", async () => {
    const { deck } = await open();
    drag(SWIPE_THRESHOLD + 10);
    await onTop("Example Role 2");
    drag(-(SWIPE_THRESHOLD + 10));
    await onTop("Example Role 3");
    expect(deck.calls.filter((c) => c.startsWith("POST"))).toEqual([
      `POST like ${makeCard(1).id}`,
      `POST pass ${makeCard(2).id}`,
    ]);
  });

  it("under the threshold springs back and sends nothing", async () => {
    const { deck } = await open();
    drag(SWIPE_THRESHOLD - 10);
    expect(screen.getByTestId("deck-card")).toHaveAttribute("data-dx", "0");
    expect(deck.calls.some((c) => c.startsWith("POST"))).toBe(false);
  });

  it("does not move the card with reduced motion: buttons only", async () => {
    reducedMotion(true);
    const { deck } = await open();
    drag(SWIPE_THRESHOLD + 50);
    expect(screen.getByTestId("deck-card")).toHaveAttribute("data-dx", "0");
    expect(screen.getByTestId("deck-card").style.transform).toBe("");
    expect(deck.calls.some((c) => c.startsWith("POST"))).toBe(false);
    await userEvent.click(likeButton());
    await onTop("Example Role 2");
  });
});

describe("pages and the empty deck", () => {
  it("reads the next page before the last card", async () => {
    const cards = Array.from({ length: 5 }, (_, i) => makeCard(i + 1));
    const { deck } = await open(cards, { pageSize: 3 });
    expect(screen.getByText("5 postings left")).toBeVisible();
    await userEvent.click(passButton());
    await waitFor(() => {
      expect(deck.calls).toContain("GET deck ?cursor=3");
    });
    await onTop("Example Role 2");
  });

  it("names the person's threshold with the two links", async () => {
    withPostingsAndSkills();
    fakeDeck([], { minFit: 82 });
    renderApp("/discover");
    expect(
      await screen.findByText("Nothing new above your threshold of 82."),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Find new postings" }),
    ).toHaveAttribute("href", "/jobs?find=1");
    expect(
      screen.getByRole("link", { name: "Search options" }),
    ).toHaveAttribute("href", "/profile?tab=search");
  });

  it("shows no Pass, Like, Undo last or keys without a card", async () => {
    finePointer(true);
    withPostingsAndSkills();
    fakeDeck([]);
    renderApp("/discover");
    expect(
      await screen.findByText("Nothing new above your threshold of 70."),
    ).toBeVisible();
    expect(screen.queryByTestId("deck-controls")).toBeNull();
    for (const name of ["Pass", "Like", /Undo last/])
      expect(screen.queryByRole("button", { name })).toBeNull();
    expect(screen.queryByTestId("deck-keys")).toBeNull();
  });

  it("says there is no posting yet and opens Find jobs", async () => {
    fakeJobsApi([]);
    fakeProfile();
    fakeDeck([]);
    renderApp("/discover");
    expect(await screen.findByText("No postings yet.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Find jobs" })).toHaveAttribute(
      "href",
      "/jobs?find=1",
    );
    expect(
      screen.queryByText(/Nothing new above your threshold/),
    ).not.toBeInTheDocument();
  });

  it("says a profile without skills keeps every posting under the threshold", async () => {
    fakeJobsApi([makeJob(1)]);
    fakeProfile({});
    fakeDeck([], { minFit: 70 });
    renderApp("/discover");
    expect(
      await screen.findByText(
        "Your profile has no skills yet, so new postings score close to 0 and stay under your threshold of 70.",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Add your skills" }),
    ).toHaveAttribute("href", "/profile?tab=skills");
    expect(screen.getByText(/Rescore all postings/)).toBeVisible();
  });

  it("empties while open when the last card is swiped", async () => {
    await open([makeCard(1)]);
    await userEvent.click(likeButton());
    expect(
      await screen.findByText("Nothing new above your threshold of 70."),
    ).toBeVisible();
  });
});

describe("filters", () => {
  it("live under Advanced and read the deck again with them", async () => {
    const { deck } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Permanent" }));
    await userEvent.selectOptions(
      screen.getByLabelText("Remote or on site"),
      "remote",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Source"),
      "greenhouse",
    );
    await waitFor(() => {
      expect(deck.calls).toContain(
        "GET deck ?type%5B%5D=CDI&remote=remote&source=greenhouse",
      );
    });
  });

  it("keep the arrow keys for a focused filter box: they swipe nothing", async () => {
    const { deck } = await open();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Advanced" }));
    const box = screen.getByRole("checkbox", { name: "Permanent" });
    box.focus();
    await user.keyboard("{ArrowRight}{ArrowLeft}");
    expect(deck.calls.some((call) => call.startsWith("POST"))).toBe(false);
    // A tap on the box's words ticks it too.
    await user.click(screen.getByText("Permanent", { selector: "label" }));
    expect(box).toBeChecked();
  });
});

describe("the app shell", () => {
  it("has the Discover link", async () => {
    await open();
    expect(
      within(screen.getByRole("navigation", { name: "Main" })).getByRole(
        "link",
        {
          name: "Discover",
        },
      ),
    ).toHaveAttribute("href", "/discover");
  });
});
