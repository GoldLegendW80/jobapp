import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  chatWith,
  CODE_ASK,
  SIGNIN_ASK,
  WAIT_NOW,
} from "@/design-system/fixtures/agent-wait";
import { SAID } from "@/test/agent-wait";
import { fakeQueue, queueItem } from "@/test/apply-queue";
import { fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";

import { ASK_NOT_SHOWN, CODE_BY_HAND } from "../helpers/agent-wait";
import { DECISIONS } from "../helpers/decisions";

beforeEach(() => {
  fakeSession(member);
});

async function card(title: RegExp) {
  return within(await screen.findByRole("article", { name: title }));
}

describe("the apply agent's wait on the Applications page (A25)", () => {
  it("shows the assistant's sentence, the box and the browser on the card, among those waiting for you, even after a send button", async () => {
    const asking = queueItem(1, "needs_person", {
      title: "Robot Arm Engineer",
      mode: "direct",
      needs: "agent_waits",
    });
    const afterSend = queueItem(2, "submitting", {
      title: "Drone Pilot",
      mode: "direct",
      needs: "agent_waits",
    });
    const fake = fakeQueue([asking, afterSend]);
    fake.now = WAIT_NOW;
    fake.details[asking.id] = {
      question: SIGNIN_ASK,
      chat: chatWith(SIGNIN_ASK),
    };
    fake.details[afterSend.id] = {
      question: { ...CODE_ASK, after_send: true },
      chat: chatWith(CODE_ASK),
    };
    renderApp("/applications");
    const first = await card(/Robot Arm Engineer/);
    expect(await first.findByText(SIGNIN_ASK.sentence)).toBeInTheDocument();
    expect(
      first.getByLabelText("Your answer to the assistant"),
    ).toBeInTheDocument();
    expect(
      first.getByRole("button", { name: "Watch the browser" }),
    ).toBeInTheDocument();
    expect(first.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(
      first.getByRole("button", { name: "Cancel application" }),
    ).toBeInTheDocument();

    const second = await card(/Drone Pilot/);
    expect(
      await second.findByLabelText("The code the site sent you"),
    ).toBeInTheDocument();
    expect(second.getByText(CODE_BY_HAND)).toBeInTheDocument();
    // Both wait for the person: the api lists them first, and so does the page.
    const waiting = screen
      .getByRole("heading", { name: "Waiting for you (2)" })
      .closest("section") as HTMLElement;
    expect(
      within(waiting).getByRole("article", { name: /Drone Pilot/ }),
    ).toBeInTheDocument();
  });

  it("says so on the card when the run waits and the detail carries no ask, with Cancel", async () => {
    const asking = queueItem(1, "needs_person", {
      title: "Robot Arm Engineer",
      mode: "direct",
      needs: "agent_waits",
    });
    const fake = fakeQueue([asking]);
    fake.now = WAIT_NOW;
    fake.details[asking.id] = { question: null, chat: [] };
    renderApp("/applications");
    const one = await card(/Robot Arm Engineer/);
    expect(await one.findByText(ASK_NOT_SHOWN)).toBeInTheDocument();
    expect(
      one.getByRole("button", { name: "Cancel application" }),
    ).toBeInTheDocument();
  });

  it("offers Try again and Cancel once the wait ended, Try again with its own words", async () => {
    const user = userEvent.setup();
    const asked = queueItem(1, "needs_person", {
      title: "Robot Arm Engineer",
      mode: "direct",
      needs: "agent_asked",
    });
    const fake = fakeQueue([asked]);
    fake.details[asked.id] = {
      question: { ...SIGNIN_ASK, live: false },
      chat: chatWith(SIGNIN_ASK, "person_late"),
    };
    renderApp("/applications");
    const one = await card(/Robot Arm Engineer/);
    expect(
      await one.findByText(
        "Nobody answered the assistant in time, so it stopped and gave the browser back.",
      ),
    ).toBeInTheDocument();
    expect(one.queryByLabelText("Your answer to the assistant")).toBeNull();
    expect(one.queryByRole("button", { name: "Reject" })).toBeNull();
    await user.click(one.getByRole("button", { name: "Try again" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(DECISIONS.retryAsked.sentence);
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(fake.decisions).toEqual([{ id: asked.id, action: "retry" }]);
    });
  });

  it("gives a code from the card, the box emptied at once, and shows the api's line of it", async () => {
    const user = userEvent.setup();
    const asking = queueItem(1, "needs_person", {
      title: "Robot Arm Engineer",
      mode: "direct",
      needs: "agent_waits",
    });
    const fake = fakeQueue([asking]);
    fake.now = WAIT_NOW;
    fake.details[asking.id] = { question: CODE_ASK, chat: chatWith(CODE_ASK) };
    renderApp("/applications");
    const one = await card(/Robot Arm Engineer/);
    const box = await one.findByLabelText("The code the site sent you");
    await user.type(box, "K7Q 2X9");
    await user.click(one.getByRole("button", { name: "Give the code" }));
    expect(box).toHaveValue("");
    expect(await one.findByText(SAID.codeSaid)).toBeInTheDocument();
    expect(fake.agent.sent).toEqual([
      { route: "question", body: { code: "K7Q 2X9" } },
    ]);
  });

  it("writes to the assistant from the card of a fill under way, and says whether it read it", async () => {
    const user = userEvent.setup();
    const working = queueItem(1, "filling", {
      title: "Robot Arm Engineer",
      mode: "direct",
    });
    const fake = fakeQueue([working]);
    fake.details[working.id] = { chat: [] };
    renderApp("/applications");
    const one = await card(/Robot Arm Engineer/);
    await user.type(
      await one.findByLabelText("Write to the assistant"),
      "Use the cover letter as it is.",
    );
    await user.click(one.getByRole("button", { name: "Send the message" }));
    expect(
      await one.findByText(
        "You, to the assistant. The assistant has not read it yet.",
      ),
    ).toBeInTheDocument();
    expect(fake.agent.sent).toEqual([
      { route: "messages", body: { text: "Use the cover letter as it is." } },
    ]);
  });

  it("says on the card that the run stopped because a page showed the code again", async () => {
    fakeQueue([
      queueItem(1, "submit_unknown", {
        title: "Robot Arm Engineer",
        mode: "direct",
        error_code: "code_shown",
      }),
    ]);
    renderApp("/applications");
    const one = await card(/Robot Arm Engineer/);
    expect(
      await one.findByText(
        "The code you gave showed again, so the assistant stopped, and the site may already have the application. Look for a confirmation mail or on the site.",
      ),
    ).toBeInTheDocument();
  });
});
