import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { fakeAnswer, fakeApplication, leftAnswer } from "@/test/apply";

import { AnswerEditor } from "./answer-editor";
import { statusLine } from "./apply-state";
import { FieldsList } from "./fields-list";

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
        expect(onSave).toHaveBeenCalledWith("x".repeat(998));
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
        expect(onSave).toHaveBeenCalledWith(letter);
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
    expect(onSave).toHaveBeenCalledWith(letter);
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
