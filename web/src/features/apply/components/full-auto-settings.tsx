import { CircleAlert } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { AdvancedSection } from "@/components/blocks/advanced-section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";
import { SettingsSection } from "@/components/shared/profile/settings-frame";
import { message } from "@/features/admin/helpers/errors";

import {
  HOLD_MAX_MINUTES,
  fullAutoSentence,
  readMinutes,
  rehearsalsLeft,
  rehearsalsSentence,
  waitsForYou,
} from "../helpers/full-auto-state";
import {
  holdText,
  settingsRefusalOf,
  type ApplySettings,
} from "../api/settings-api";
import { useSaveFullAutoSettings } from "../api/use-apply-settings";

/**
 * "After a Like" (SPEC-apply.md, "Frontend", Settings; tasks/apply/todo.md, A13b): Review first
 * or Full auto. Choosing Full auto first says what a Like then does, with the person's own
 * threshold, hold and company rule, and the rehearsals; only "Turn on Full auto" turns it on.
 * Review first turns it off at once: held items wait for the person in the queue. Its Advanced
 * holds the safety ceiling and the hold length.
 */
export function FullAutoSection({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveFullAutoSettings();
  const [asking, setAsking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const on = settings.mode_after_like === "full_auto";
  const chosen = asking ? "full_auto" : settings.mode_after_like;

  const change = (mode: "review" | "full_auto") => {
    setProblem(null);
    save.mutate(
      { mode_after_like: mode },
      {
        onSuccess: () => {
          setAsking(false);
          toast.success(
            mode === "full_auto"
              ? "Full auto is on."
              : "Full auto is off. Held applications wait for you in Applications.",
          );
        },
        onError: (error) => {
          setProblem(message(error));
        },
      },
    );
  };

  return (
    <SettingsSection
      title="After a Like"
      description="What the app does with a posting you like. This concerns Likes only: the Apply button of a posting follows the box above."
    >
      {problem ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{problem}</AlertDescription>
        </Alert>
      ) : null}
      <FieldSet className="gap-3" disabled={save.isPending}>
        <FieldLegend className="sr-only">Mode after a Like</FieldLegend>
        <RadioGroup
          name={`${id}-mode`}
          value={chosen}
          className="gap-3"
          onValueChange={(value) => {
            if (value === "review") {
              if (asking) setAsking(false);
              else if (on) change("review");
            } else if (value === "full_auto") {
              if (!on) setAsking(true);
            }
          }}
        >
          <Field orientation="horizontal" className="gap-2">
            <RadioGroupItem
              id={`${id}-review`}
              value="review"
              aria-describedby={`${id}-review-text`}
            />
            <FieldContent className="gap-0.5">
              <FieldLabel htmlFor={`${id}-review`}>Review first</FieldLabel>
              <FieldDescription id={`${id}-review-text`}>
                The app fills the form and waits for your approval before it
                submits.
              </FieldDescription>
            </FieldContent>
          </Field>
          <Field orientation="horizontal" className="gap-2">
            <RadioGroupItem
              id={`${id}-full`}
              value="full_auto"
              aria-describedby={`${id}-full-text`}
            />
            <FieldContent className="gap-0.5">
              <FieldLabel htmlFor={`${id}-full`}>Full auto</FieldLabel>
              <FieldDescription id={`${id}-full-text`}>
                Your Like is your approval: the app fills the form, holds it so
                you can cancel, checks it once more, then submits it for you.
              </FieldDescription>
            </FieldContent>
          </Field>
        </RadioGroup>
      </FieldSet>

      {asking || on ? (
        <section
          aria-label={on ? "Full auto is on" : "Turn on Full auto"}
          className="flex max-w-prose flex-col gap-3 text-sm"
        >
          <p className="font-medium">{fullAutoSentence(settings)}</p>
          <p id={`${id}-waits`}>These wait for you in Applications instead:</p>
          <ul
            aria-labelledby={`${id}-waits`}
            className="flex list-disc flex-col gap-1 pl-5"
          >
            {waitsForYou(settings).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p>
            A company cap you set on this page replaces the one per company rule
            for that company.
          </p>
          {!on || rehearsalsLeft(settings) > 0 ? (
            <p>{rehearsalsSentence(settings)}</p>
          ) : null}
          {settings.paused ? (
            <p className="text-warning">
              Automatic applications are paused: nothing is submitted until you
              turn the pause off.
            </p>
          ) : null}
          {asking ? (
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button
                className="w-full sm:w-fit"
                disabled={save.isPending}
                aria-busy={save.isPending}
                onClick={() => {
                  change("full_auto");
                }}
              >
                {save.isPending ? (
                  <Spinner aria-hidden="true" role={undefined} />
                ) : null}
                Turn on Full auto
              </Button>
              <Button
                variant="outline"
                className="w-full sm:w-fit"
                disabled={save.isPending}
                onClick={() => {
                  setAsking(false);
                  setProblem(null);
                }}
              >
                Keep Review first
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}
      <HoldSettings settings={settings} />
    </SettingsSection>
  );
}

/**
 * Advanced: the safety ceiling (read only: raising it is the owner's call) and the hold length,
 * never below the server's minimum.
 */
function HoldSettings({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveFullAutoSettings();
  const minMinutes = Math.ceil(settings.hold_min_s / 60);
  const [typed, setTyped] = useState(String(Math.round(settings.hold_s / 60)));
  const [problem, setProblem] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const minutes = readMinutes(typed, minMinutes);
    if (minutes === null) {
      setProblem(
        `Type a whole number of minutes from ${minMinutes} to ${HOLD_MAX_MINUTES}.`,
      );
      document.getElementById(`${id}-hold`)?.focus();
      return;
    }
    setProblem(null);
    setFailed(null);
    save.mutate(
      { hold_s: minutes * 60 },
      {
        onSuccess: (saved) => {
          setTyped(String(Math.round(saved.hold_s / 60)));
          toast.success("Hold length saved.");
        },
        onError: (error) => {
          setFailed(settingsRefusalOf(error)?.detail ?? message(error));
        },
      },
    );
  };
  const unchanged = readMinutes(typed, minMinutes) === settings.hold_s / 60;
  return (
    <AdvancedSection>
      <dl className="grid max-w-prose gap-3 text-sm">
        <div className="flex flex-col gap-0.5">
          <dt className="font-medium">Safety ceiling</dt>
          <dd className="text-muted-foreground">
            At most {settings.safety_ceiling} submissions a day in Full auto.
            The rest of the day waits for you.
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="font-medium">Hold length</dt>
          <dd className="text-muted-foreground">
            {holdText(settings.hold_s)} between a Full auto decision and its
            submission, so you can cancel it. Never less than{" "}
            {holdText(settings.hold_min_s)}.
          </dd>
        </div>
      </dl>
      <form
        noValidate
        aria-label="Hold length"
        className="flex flex-col gap-3"
        onSubmit={onSubmit}
      >
        {failed ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{failed}</AlertDescription>
          </Alert>
        ) : null}
        <Field className="gap-2">
          <FieldLabel htmlFor={`${id}-hold`}>Hold length in minutes</FieldLabel>
          <div className="flex flex-wrap gap-2">
            <Input
              id={`${id}-hold`}
              inputMode="numeric"
              maxLength={4}
              className="w-24"
              value={typed}
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? `${id}-problem` : undefined}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
            />
            <Button
              type="submit"
              disabled={save.isPending || unchanged}
              aria-busy={save.isPending}
            >
              {save.isPending ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Save hold length
            </Button>
          </div>
          {problem ? (
            <FieldError id={`${id}-problem`}>{problem}</FieldError>
          ) : null}
        </Field>
      </form>
    </AdvancedSection>
  );
}
