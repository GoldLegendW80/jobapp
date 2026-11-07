import { SendHorizontal } from "lucide-react";
import {
  useId,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";

import { Field, FieldLabel } from "@/components/ui/field";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupText,
  InputGroupTextarea,
} from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import { Spinner } from "@/components/ui/spinner";

const SEND_KEYS = (
  <>
    <Kbd>Enter</Kbd> sends, <Kbd>Shift</Kbd> and <Kbd>Enter</Kbd> adds a line.
  </>
);

/**
 * The box a chat message is typed in, under every conversation of the app: Send inside it and,
 * under the text, a hint (the keys by default) read as the box's description and announced when
 * it changes, and the count of characters against `max` when `showCount`. Enter and Send both
 * call `onSubmit`, which decides what a message that cannot be sent yet means.
 */
export function ChatPrompt({
  value,
  max,
  showCount = false,
  hint = SEND_KEYS,
  placeholder,
  disabled = false,
  canSend,
  sending,
  textareaRef,
  className,
  onChange,
  onSubmit,
}: {
  value: string;
  max: number;
  showCount?: boolean;
  hint?: ReactNode;
  placeholder: string;
  /** The box takes no text (no consent, no model credential). */
  disabled?: boolean;
  canSend: boolean;
  sending: boolean;
  textareaRef?: Ref<HTMLTextAreaElement>;
  className?: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const ids = { box: useId(), hint: useId() };
  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    onSubmit();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };
  return (
    <form onSubmit={submit} className={className} aria-label="Send a message">
      <Field>
        <FieldLabel htmlFor={ids.box} className="sr-only">
          Message
        </FieldLabel>
        <InputGroup data-disabled={disabled}>
          <InputGroupTextarea
            id={ids.box}
            ref={textareaRef}
            value={value}
            maxLength={max}
            rows={2}
            placeholder={placeholder}
            className="max-h-40 min-h-11"
            aria-describedby={ids.hint}
            disabled={disabled}
            onChange={(event) => {
              onChange(event.target.value);
            }}
            onKeyDown={onKeyDown}
          />
          <InputGroupAddon align="block-end" className="gap-3">
            <InputGroupText
              id={ids.hint}
              className="min-w-0 flex-1 flex-wrap justify-between gap-x-3 gap-y-1 text-xs font-normal"
            >
              {/* The count stays out: a live count would be read at every key. */}
              <span aria-live="polite" aria-atomic="true">
                {hint}
              </span>
              {showCount ? (
                <span className="tabular-nums">
                  {value.length} of {max}
                </span>
              ) : null}
            </InputGroupText>
            <InputGroupButton
              type="submit"
              variant="default"
              size="icon-sm"
              disabled={!canSend}
              aria-busy={sending}
            >
              {sending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : (
                <SendHorizontal aria-hidden="true" />
              )}
              <span className="sr-only">Send</span>
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
      </Field>
    </form>
  );
}
