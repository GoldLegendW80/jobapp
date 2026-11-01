import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { message } from "@/features/admin/errors";

import {
  HOLD_MAX_MINUTES,
  fullAutoSentence,
  readMinutes,
  rehearsalsLeft,
  rehearsalsSentence,
  waitsForYou,
} from "./full-auto-state";
import { queueKey } from "./queue-api";
import {
  applySettingsKey,
  holdText,
  saveApplySettings,
  settingsRefusalOf,
  type ApplySettings,
} from "./settings-api";

function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveApplySettings,
    onSuccess: (saved) => {
      client.setQueryData<ApplySettings>(applySettingsKey, saved);
      // Turning Full auto off sends held items to the queue as review items.
      void client.invalidateQueries({ queryKey: queueKey });
    },
  });
}

/**
 * "After a Like" (SPEC-apply.md, "Frontend", Settings; tasks/apply/todo.md, A13b): Review first
 * or Full auto. Choosing Full auto first says what a Like then does, with the person's own
 * threshold, hold and company rule, and the rehearsals; only "Turn on Full auto" turns it on.
 * Review first turns it off at once: held items wait for the person in the queue.
 */
export function FullAutoModeCard({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveSettings();
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
    <Card aria-labelledby={`${id}-title`}>
      <CardHeader>
        <CardTitle>
          <h2 id={`${id}-title`} className="text-lg">
            After a Like
          </h2>
        </CardTitle>
        <CardDescription>
          What the app does with a posting you like. This concerns Likes only:
          the Apply button of a posting follows the box above.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {problem ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        ) : null}
        <fieldset className="flex flex-col gap-3" disabled={save.isPending}>
          <legend className="sr-only">Mode after a Like</legend>
          <div className="flex items-start gap-2">
            <input
              id={`${id}-review`}
              type="radio"
              name={`${id}-mode`}
              value="review"
              className="mt-0.5 size-4 shrink-0 accent-primary"
              checked={chosen === "review"}
              aria-describedby={`${id}-review-text`}
              onChange={() => {
                if (asking) setAsking(false);
                else if (on) change("review");
              }}
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor={`${id}-review`}>Review first</Label>
              <p
                id={`${id}-review-text`}
                className="text-sm text-muted-foreground"
              >
                The app fills the form and waits for your approval before it
                submits.
              </p>
            </div>
          </div>
          <div className="flex items-start gap-2">
            <input
              id={`${id}-full`}
              type="radio"
              name={`${id}-mode`}
              value="full_auto"
              className="mt-0.5 size-4 shrink-0 accent-primary"
              checked={chosen === "full_auto"}
              aria-describedby={`${id}-full-text`}
              onChange={() => {
                if (!on) setAsking(true);
              }}
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor={`${id}-full`}>Full auto</Label>
              <p
                id={`${id}-full-text`}
                className="text-sm text-muted-foreground"
              >
                Your Like is your approval: the app fills the form, holds it so
                you can cancel, checks it once more, then submits it for you.
              </p>
            </div>
          </div>
        </fieldset>

        {asking || on ? (
          <section
            aria-label={on ? "Full auto is on" : "Turn on Full auto"}
            className="flex flex-col gap-3 rounded-md border p-3 text-sm"
          >
            <p className="font-medium">{fullAutoSentence(settings)}</p>
            <p id={`${id}-waits`}>
              These wait for you in Applications instead:
            </p>
            <ul
              aria-labelledby={`${id}-waits`}
              className="flex list-disc flex-col gap-1 pl-5"
            >
              {waitsForYou(settings).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <p>
              A company cap you set on this page replaces the one per company
              rule for that company.
            </p>
            {!on || rehearsalsLeft(settings) > 0 ? (
              <p>{rehearsalsSentence(settings)}</p>
            ) : null}
            {settings.paused ? (
              <p className="text-amber-800 dark:text-amber-300">
                Automatic applications are paused: nothing is submitted until
                you turn the pause off.
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
                    <LoaderCircle aria-hidden="true" className="animate-spin" />
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
      </CardContent>
    </Card>
  );
}

/**
 * Advanced: the safety ceiling (read only: raising it is the owner's call) and the hold length,
 * never below the server's minimum.
 */
export function FullAutoAdvanced({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveSettings();
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
    <Card>
      <CardContent>
        <details className="group">
          <summary className="cursor-pointer rounded-md text-lg font-semibold">
            Advanced
          </summary>
          <dl className="mt-4 grid gap-3 text-sm">
            <div className="flex flex-col gap-0.5">
              <dt className="font-medium">Safety ceiling</dt>
              <dd className="text-muted-foreground">
                At most {settings.safety_ceiling} submissions a day in Full
                auto. The rest of the day waits for you.
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
            className="mt-4 flex flex-col gap-3"
            onSubmit={onSubmit}
          >
            {failed ? (
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>{failed}</AlertDescription>
              </Alert>
            ) : null}
            <div className="grid gap-2">
              <Label htmlFor={`${id}-hold`}>Hold length in minutes</Label>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                  id={`${id}-hold`}
                  inputMode="numeric"
                  maxLength={4}
                  className="sm:w-28"
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
                    <LoaderCircle aria-hidden="true" className="animate-spin" />
                  ) : null}
                  Save hold length
                </Button>
              </div>
              {problem ? (
                <p id={`${id}-problem`} className="text-sm text-destructive">
                  {problem}
                </p>
              ) : null}
            </div>
          </form>
        </details>
      </CardContent>
    </Card>
  );
}
