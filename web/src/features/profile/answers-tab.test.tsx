import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { afterEach, describe, expect, it } from "vitest";

import { admin, fakeSession } from "@/test/auth";
import { fakeProfile, haraldLike } from "@/test/profile";
import { renderApp } from "@/test/render";

import type { Answers } from "./api";

afterEach(() => {
  toast.dismiss();
});

async function openAnswers(
  initial = haraldLike() as Parameters<typeof fakeProfile>[0],
) {
  fakeSession(admin);
  const state = fakeProfile(initial);
  const user = userEvent.setup();
  renderApp("/profile?tab=answers");
  await screen.findByRole("form", { name: "Answers" });
  return { user, state };
}

const save = () => screen.getByRole("button", { name: "Save answers" });
const saved = (state: ReturnType<typeof fakeProfile>, index = 0) =>
  state.saves[index]!.body.data as Answers;

describe("answers tab, the apply fields", () => {
  it("shows the stored values with labelled controls", async () => {
    await openAnswers();
    expect(screen.getByLabelText("Preferred name")).toHaveValue("Ana");
    expect(screen.getByLabelText("Pronouns")).toHaveValue("she/her");
    expect(screen.getByLabelText("Year of the highest degree")).toHaveValue(
      "2022",
    );
    expect(screen.getByLabelText("Willing to work on site")).toHaveValue("yes");
    expect(screen.getByLabelText("Consent to a background check")).toHaveValue(
      "no",
    );
    expect(screen.getByLabelText("What to do")).toHaveValue("stored");
    expect(screen.getByLabelText("Salary stance")).toHaveValue("range");
    expect(screen.getAllByLabelText(/^Currency/)[0]).toHaveValue("EUR");
  });

  it("saves what was typed and chosen, and keeps the rest", async () => {
    const { user, state } = await openAnswers();
    const name = screen.getByLabelText("Preferred name");
    await user.clear(name);
    await user.type(name, "Ani");
    await user.selectOptions(
      screen.getByLabelText("Salary stance"),
      "sentence",
    );
    await user.selectOptions(
      screen.getByLabelText("Consent to a reference check"),
      "",
    );
    await user.click(save());
    await waitFor(() => {
      expect(state.saves).toHaveLength(1);
    });
    const body = saved(state);
    expect(body.preferred_name).toBe("Ani");
    expect(body.salary_stance).toBe("sentence");
    expect(body.reference_check_consent).toBeNull();
    expect(body.salary_ranges).toHaveLength(2);
    expect(body.eeo_answers).toEqual([{ topic: "gender", answer: "Woman" }]);
    expect(body.eeo_policy).toBe("stored");
  });

  it("adds a salary range from the keyboard and refuses a bad currency", async () => {
    const { user, state } = await openAnswers();
    await user.click(screen.getByRole("button", { name: "Add range" }));
    const currencies = screen.getAllByLabelText(/^Currency/);
    const fresh = currencies[currencies.length - 1]!;
    expect(fresh).toHaveFocus();
    await user.type(fresh, "chf");
    await user.click(save());
    expect(
      await screen.findByText(/three capital letters/),
    ).toBeInTheDocument();
    expect(state.saves).toHaveLength(0);
  });

  it("shows the stored answers only under the stored policy", async () => {
    const { user } = await openAnswers();
    expect(screen.getByLabelText(/^Answer for topic 1/)).toHaveValue("Woman");
    await user.selectOptions(screen.getByLabelText("What to do"), "decline");
    expect(screen.queryByLabelText(/^Answer for topic 1/)).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Add answer" }),
    ).not.toBeInTheDocument();
  });

  it("opens a profile saved before the fields with the defaults", async () => {
    const legacy = haraldLike();
    const added = [
      "preferred_name",
      "pronouns",
      "willing_onsite",
      "eeo_policy",
      "eeo_answers",
      "salary_stance",
      "salary_ranges",
    ];
    const answers = Object.fromEntries(
      Object.entries(legacy.answers).filter(([name]) => !added.includes(name)),
    );
    await openAnswers({ ...legacy, answers });
    expect(screen.getByLabelText("Preferred name")).toHaveValue("");
    expect(screen.getByLabelText("Willing to work on site")).toHaveValue("");
    expect(screen.getByLabelText("What to do")).toHaveValue("decline");
    expect(screen.getByLabelText("Salary stance")).toHaveValue("");
  });
});
