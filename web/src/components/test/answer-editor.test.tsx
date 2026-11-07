import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { fakeAnswer, fakeApplication, leftAnswer } from "@/test/apply";

import { AnswerEditor } from "@/components/shared/apply/answer-editor";
import { statusLine } from "@/features/apply/helpers/apply-state";
import { FieldsList } from "@/components/shared/apply/fields-list";

describe("a yes or no", () => {
  const editor = (value: unknown, onSave = vi.fn().mockResolvedValue(true)) => {
    render(
      <AnswerEditor
        answer={fakeAnswer("remote", "Open to remote work", value, {
          kind: "yes_no",
        })}
        saving={false}
        onSave={onSave}
        onDirtyChange={vi.fn()}
        remember="on"
      />,
    );
    return onSave;
  };

  it("is one choice named by the question, the saved answer checked", () => {
    editor(true);
    const choice = screen.getByRole("radiogroup", {
      name: "Answer for Open to remote work",
    });
    expect(
      screen.getAllByRole("radio").map((radio) => radio.textContent),
    ).toEqual(["Yes", "No"]);
    expect(screen.getByRole("radio", { name: "Yes" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: "No" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(choice).toBeInTheDocument();
  });

  it("saves the other answer at once, with the box to remember it, and never clears it", async () => {
    const user = userEvent.setup();
    const onSave = editor(true);
    await user.click(screen.getByRole("radio", { name: "No" }));
    expect(onSave).toHaveBeenCalledWith(false, true);
    // The chosen answer clicked again stays chosen: nothing is saved.
    await user.click(screen.getByRole("radio", { name: "Yes" }));
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("starts with neither checked when there is no answer yet, and the keyboard reaches it", async () => {
    const user = userEvent.setup();
    const onSave = editor(null);
    for (const radio of screen.getAllByRole("radio"))
      expect(radio).toHaveAttribute("aria-checked", "false");
    await user.tab();
    await user.tab();
    expect(screen.getByRole("radio", { name: "Yes" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "No" })).toHaveFocus();
    await user.keyboard(" ");
    expect(onSave).toHaveBeenCalledWith(false, true);
  });
});

describe("document letter edits", () => {
  it.each([998, 999])(
    "honours the email subject limit at %i characters",
    (length) => {
      const onSave = vi.fn().mockResolvedValue(true);
      render(
        <AnswerEditor
          answer={fakeAnswer("email_subject", "Email subject", "Original", {
            max_chars: 998,
          })}
          saving={false}
          onSave={onSave}
          onDirtyChange={vi.fn()}
        />,
      );
      const box = screen.getByRole("textbox");
      expect(box).toHaveAttribute("maxlength", "998");
      fireEvent.change(box, { target: { value: "x".repeat(length) } });
      const save = screen.getByRole("button", { name: "Save answer" });
      if (length === 998) {
        expect(save).toBeEnabled();
        fireEvent.click(save);
        expect(onSave).toHaveBeenCalledWith("x".repeat(998), false);
      } else {
        expect(save).toBeDisabled();
        expect(onSave).not.toHaveBeenCalled();
      }
    },
  );

  it("shows saved Unicode text whole and counts the native editor's units", () => {
    const value = "🤖".repeat(5001);
    render(
      <AnswerEditor
        answer={fakeAnswer("letter", "Cover letter", value, {
          max_chars: 10000,
        })}
        saving={false}
        onSave={vi.fn()}
        onDirtyChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveValue(value);
    expect(screen.getByText(/^10002 \/ 10000 characters/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Shorten this answer");
    expect(screen.getByRole("button", { name: "Save answer" })).toBeDisabled();
  });
  it.each([5000, 5001, 10000, 10001])(
    "handles a %i character letter without cutting it",
    (length) => {
      const onSave = vi.fn().mockResolvedValue(true);
      render(
        <AnswerEditor
          answer={fakeAnswer("letter", "Cover letter", "original", {
            max_chars: 10000,
          })}
          saving={false}
          onSave={onSave}
          onDirtyChange={vi.fn()}
        />,
      );
      const box = screen.getByRole("textbox");
      const letter = "x".repeat(length);
      fireEvent.change(box, { target: { value: letter } });
      expect(box).toHaveValue(letter);
      const save = screen.getByRole("button", { name: "Save answer" });
      if (length <= 10000) {
        expect(save).toBeEnabled();
        fireEvent.click(save);
        expect(onSave).toHaveBeenCalledWith(letter, false);
      } else {
        expect(save).toBeDisabled();
        expect(screen.getByRole("alert")).toHaveTextContent("10000 characters");
      }
    },
  );

  it.each(["a\tb", "a\rb"])("explains an untypable edited letter", (value) => {
    render(
      <AnswerEditor
        answer={fakeAnswer("letter", "Cover letter", "original", {
          max_chars: 10000,
        })}
        saving={false}
        onSave={vi.fn()}
        onDirtyChange={vi.fn()}
      />,
    );
    fireEvent.paste(screen.getByRole("textbox"), {
      clipboardData: { getData: () => value },
    });
    expect(screen.getByRole("button", { name: "Save answer" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Remove tabs, carriage returns",
    );
  });

  it("keeps an ordinary field at 5000 even when its label says cover letter", () => {
    render(
      <AnswerEditor
        answer={fakeAnswer("letter", "Cover letter", "original")}
        saving={false}
        onSave={vi.fn()}
        onDirtyChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveAttribute("maxlength", "5000");
  });

  it.each([
    ["letter_too_long", /The letter has more than 10000 characters/],
    ["letter_control_character", /The letter contains a tab, carriage return/],
  ])("shows why the document letter was left empty", (reason, sentence) => {
    render(
      <FieldsList
        answers={[
          leftAnswer("letter", "Cover letter", {
            max_chars: 10000,
            left_reason: reason,
          }),
        ]}
        editable
        saving={false}
        onSave={vi.fn()}
        onDirtyChange={vi.fn()}
      />,
    );
    expect(screen.getByText(sentence)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("keeps the server's 10000 character allowance after a person edits the letter", () => {
    const onSave = vi.fn().mockResolvedValue(true);
    render(
      <AnswerEditor
        answer={fakeAnswer("letter", "Additional information", "original", {
          source: "person",
          max_chars: 10000,
        })}
        saving={false}
        onSave={onSave}
        onDirtyChange={vi.fn()}
      />,
    );
    const box = screen.getByRole("textbox");
    expect(box).toHaveAttribute("maxlength", "10000");
    expect(box.tagName).toBe("TEXTAREA");
    const letter = " x" + "a".repeat(9996) + "x ";
    fireEvent.change(box, { target: { value: letter } });
    fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
    expect(onSave).toHaveBeenCalledWith(letter, false);
    expect(screen.getByText(/^10000 \/ 10000 characters/)).toBeInTheDocument();
  });

  it("explains the document render deadline", () => {
    expect(
      statusLine(
        fakeApplication("failed", { error_code: "documents_rendering" }),
      ),
    ).toContain("your CV or letter was still being prepared after an hour");
  });
});
