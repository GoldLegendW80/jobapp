/**
 * "Schedule" next to Find jobs: turn the automatic search on or off, pick its days and times
 * (one to four) and its time zone, and see when it runs next. Saved as `search.sweep_schedule`
 * of the profile (PUT /api/profile/search with the base revision; a change made meanwhile is
 * merged once, see saveSchedule). Off by default: nothing runs until the user turns it on.
 */
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CircleAlert, Plus, X } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { describeSchedule } from "@/features/profile/schedule";

import {
  DEFAULT_SCHEDULE,
  formatInZone,
  nextRun,
  TIMES_MAX,
  timeZones,
  WEEKDAY_LABELS,
  WEEKDAYS,
  type SweepSchedule,
  type Weekday,
} from "./schedule";
import {
  profileQueryKey,
  saveSchedule,
  sweepsKey,
  type ScheduleState,
} from "./sweeps-api";
import { scheduleLine, scheduleOf, useSchedule } from "./use-schedule";

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

export function ScheduleButton() {
  const [open, setOpen] = useState(false);
  const state = useSchedule();
  return (
    <>
      <Button
        variant="outline"
        onClick={() => {
          setOpen(true);
        }}
        title={scheduleLine(state)}
      >
        <CalendarClock aria-hidden="true" />
        Schedule
        <span
          className={cn(
            "rounded-full px-1.5 text-xs font-medium",
            state?.enabled
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground",
          )}
        >
          {state?.enabled ? "On" : "Off"}
        </span>
      </Button>
      {open ? (
        <ScheduleDialog
          state={state}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}

function initial(state: ScheduleState | undefined): SweepSchedule {
  return state ? scheduleOf(state) : { ...DEFAULT_SCHEDULE };
}

export function ScheduleDialog({
  state,
  onClose,
}: {
  state: ScheduleState | undefined;
  onClose: () => void;
}) {
  const id = useId();
  const client = useQueryClient();
  const [draft, setDraft] = useState<SweepSchedule>(() => initial(state));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zones] = useState(() => timeZones(draft.timezone));

  const badTimes = draft.times.some((t) => !CLOCK.test(t));
  const invalid = !draft.days.length || !draft.times.length || badTimes;
  const next = invalid ? null : nextRun(draft, new Date());

  const toggleDay = (day: Weekday) => {
    setDraft((old) => ({
      ...old,
      days: old.days.includes(day)
        ? old.days.filter((d) => d !== day)
        : WEEKDAYS.filter((d) => d === day || old.days.includes(d)),
    }));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (invalid && draft.enabled) return;
    setSaving(true);
    setError(null);
    // Turning it off with a half-edited form keeps the defaults rather than refusing.
    const sent: SweepSchedule =
      invalid && !draft.enabled
        ? { ...DEFAULT_SCHEDULE, timezone: draft.timezone }
        : { ...draft, times: [...new Set(draft.times)].sort() };
    saveSchedule(sent).then(
      () => {
        setSaving(false);
        void client.invalidateQueries({ queryKey: sweepsKey });
        void client.invalidateQueries({ queryKey: profileQueryKey });
        toast.success(`Automatic search: ${describeSchedule(sent)}.`);
        onClose();
      },
      (failure: unknown) => {
        setSaving(false);
        setError(
          isApiError(failure) && failure.status === 409
            ? "Your profile changed in another tab twice in a row. Close and try again."
            : isApiError(failure) && failure.status === 422
              ? `Not saved: ${errorDetail(failure) ?? "the schedule is not valid"}.`
              : `Not saved. ${describeError(failure)}`,
        );
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg [&>*]:min-w-0">
        <DialogHeader>
          <DialogTitle>Automatic search</DialogTitle>
          <DialogDescription>
            The app sweeps the boards of your profile by itself at these times,
            as Find jobs does. It is off until you turn it on.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-4">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              className="size-4 accent-primary"
              checked={draft.enabled}
              onChange={(event) => {
                const enabled = event.target.checked;
                setDraft((old) => ({ ...old, enabled }));
              }}
            />
            Search automatically
          </label>

          <fieldset className="grid gap-1.5" disabled={!draft.enabled}>
            <legend className="mb-1.5 text-sm font-medium">Days</legend>
            <div className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((day) => {
                const pressed = draft.days.includes(day);
                return (
                  <Button
                    key={day}
                    type="button"
                    size="sm"
                    variant={pressed ? "default" : "outline"}
                    aria-pressed={pressed}
                    aria-label={day.charAt(0).toUpperCase() + day.slice(1)}
                    onClick={() => {
                      toggleDay(day);
                    }}
                  >
                    {WEEKDAY_LABELS[day]}
                  </Button>
                );
              })}
            </div>
            {!draft.days.length ? (
              <p className="text-xs text-destructive">Pick at least one day.</p>
            ) : null}
          </fieldset>

          <fieldset className="grid gap-1.5" disabled={!draft.enabled}>
            <legend className="mb-1.5 text-sm font-medium">Times</legend>
            <div className="flex flex-wrap items-center gap-2">
              {draft.times.map((time, index) => (
                <div key={index} className="flex items-center gap-1">
                  <Input
                    type="time"
                    aria-label={`Time ${index + 1}`}
                    className="w-28"
                    value={time}
                    required
                    onChange={(event) => {
                      const value = event.target.value;
                      setDraft((old) => ({
                        ...old,
                        times: old.times.map((t, i) =>
                          i === index ? value : t,
                        ),
                      }));
                    }}
                  />
                  {draft.times.length > 1 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove time ${index + 1}`}
                      onClick={() => {
                        setDraft((old) => ({
                          ...old,
                          times: old.times.filter((_, i) => i !== index),
                        }));
                      }}
                    >
                      <X aria-hidden="true" />
                    </Button>
                  ) : null}
                </div>
              ))}
              {draft.times.length < TIMES_MAX ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setDraft((old) => ({
                      ...old,
                      times: [...old.times, "18:00"],
                    }));
                  }}
                >
                  <Plus aria-hidden="true" />
                  Add a time
                </Button>
              ) : null}
            </div>
            {badTimes ? (
              <p className="text-xs text-destructive">
                Every time needs hours and minutes.
              </p>
            ) : null}
          </fieldset>

          <div className="grid gap-1.5">
            <Label htmlFor={`${id}-zone`}>Time zone</Label>
            <NativeSelect
              id={`${id}-zone`}
              value={draft.timezone}
              disabled={!draft.enabled}
              onChange={(event) => {
                const timezone = event.target.value;
                setDraft((old) => ({ ...old, timezone }));
              }}
            >
              {zones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </NativeSelect>
          </div>

          <p role="status" aria-live="polite" className="text-sm">
            {!draft.enabled
              ? "Automatic search is off."
              : next
                ? `Next automatic search: ${formatInZone(next, draft.timezone)}.`
                : "Pick at least one day and one time."}
          </p>
          {draft.enabled && state && !state.active && state.enabled ? (
            <p className="text-xs text-muted-foreground">
              No board is enabled in your profile yet: nothing runs until one
              is.
            </p>
          ) : null}

          {error ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={`${id}-form`}
            disabled={saving || (draft.enabled && invalid)}
          >
            {saving ? "Saving" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
