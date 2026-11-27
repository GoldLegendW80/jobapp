/**
 * The schedule of one search: on or off, its days and times (one to four), and when it runs next.
 * Off by default: nothing runs until the person turns it on.
 *
 * The main search's ("Automatic search" in the Jobs page's More actions menu, and its row in Find
 * jobs) is `search.sweep_schedule` of the profile, with the time zone every search shares (PUT
 * /api/profile/search with the base revision; a change made meanwhile is merged once, see
 * saveSchedule). A saved search's (its row in Find jobs, jobs J33) is written with the rest of it,
 * on the revision its row showed (PUT /api/jobs/searches/{id}); its time zone is the person's.
 */
import { CircleAlert, Plus, X } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Field,
  FieldError,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { NativeSelect } from "@/components/ui/native-select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { describeError, errorDetail, isApiError } from "@/lib/api";

import { describeSchedule } from "@/features/profile/helpers/schedule";

import {
  DEFAULT_SCHEDULE,
  formatInZone,
  nextRun,
  ownTimeZone,
  TIMES_MAX,
  timeZonesOwnFirst,
  WEEKDAY_LABELS,
  WEEKDAYS,
  type SweepSchedule,
} from "@/features/jobs/helpers/schedule";
import type { SavedSearch } from "@/features/jobs/api/searches-api";
import { type ScheduleState } from "@/features/jobs/api/sweeps-api";
import {
  useReadSearches,
  useScheduleSearch,
} from "@/features/jobs/api/use-saved-searches";
import { scheduleOf, useSaveSchedule } from "@/features/jobs/api/use-schedule";
import {
  SEARCH_GONE,
  scheduleFailure,
} from "@/features/jobs/helpers/saved-searches";

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

function initial(state: ScheduleState | undefined): SweepSchedule {
  return state ? scheduleOf(state) : { ...DEFAULT_SCHEDULE };
}

export function ScheduleDialog({
  state,
  search,
  onClose,
}: {
  /** The main search's schedule; a saved search's is its own `schedule`. */
  state: ScheduleState | undefined;
  /** A saved search, as its row shows it now; absent for the main search. */
  search?: SavedSearch;
  onClose: () => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState<SweepSchedule>(() =>
    initial(search ? search.schedule : state),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const [zones] = useState(() =>
    timeZonesOwnFirst(ownTimeZone(), draft.timezone),
  );
  // A saved search read again after a conflict: the next save goes on its stored revision.
  const [fresh, setFresh] = useState<SavedSearch | null>(null);
  const readSearches = useReadSearches();
  const target =
    search && fresh && fresh.revision > search.revision ? fresh : search;
  const shown = target ? target.schedule : state;
  const done = (title: string, sent: SweepSchedule) => {
    setSaving(false);
    toast.success(`${title}: ${describeSchedule(sent)}.`);
    onClose();
  };
  const saveMain = useSaveSchedule({
    onSuccess: (_saved, sent) => {
      done("Automatic search", sent);
    },
    onError: (failure) => {
      setSaving(false);
      setError(
        isApiError(failure) && failure.status === 409
          ? "Your profile changed in another tab twice in a row. Close and try again."
          : isApiError(failure) && failure.status === 422
            ? `Not saved: ${errorDetail(failure) ?? "the schedule is not valid"}.`
            : `Not saved. ${describeError(failure)}`,
      );
    },
  });
  const saveSaved = useScheduleSearch({
    onSuccess: (saved) => {
      done(`Automatic search of ${saved.name}`, scheduleOf(saved.schedule));
    },
    onError: (failure) => {
      const words = scheduleFailure(failure);
      setGone(words.gone);
      setError(words.text);
      if (!words.moved || !search) {
        setSaving(false);
        return;
      }
      readSearches()
        .then(
          (list) => {
            const found = list.items.find((item) => item.id === search.id);
            if (found) setFresh(found);
            else {
              setGone(true);
              setError(SEARCH_GONE);
            }
          },
          () => undefined,
        )
        .finally(() => {
          setSaving(false);
        });
    },
  });

  const badTimes = draft.times.some((t) => !CLOCK.test(t));
  const invalid = !draft.days.length || !draft.times.length || badTimes;
  const next = invalid ? null : nextRun(draft, new Date());

  const setDays = (days: string[]) => {
    setDraft((old) => ({
      ...old,
      days: WEEKDAYS.filter((day) => days.includes(day)),
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
    if (target)
      saveSaved.mutate({
        search: target,
        schedule: { enabled: sent.enabled, days: sent.days, times: sent.times },
      });
    else saveMain.mutate(sent);
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
          <DialogTitle>
            {search ? `Automatic search of ${search.name}` : "Automatic search"}
          </DialogTitle>
          <DialogDescription>
            The app runs {search ? search.name : "your main search"} by itself
            at these times, as Run now does. It is off until you turn it on.
          </DialogDescription>
        </DialogHeader>
        <form id={`${id}-form`} onSubmit={submit} className="grid gap-4">
          <Field orientation="horizontal" className="gap-2">
            <Checkbox
              id={`${id}-enabled`}
              checked={draft.enabled}
              onCheckedChange={(checked) => {
                const enabled = checked === true;
                setDraft((old) => ({ ...old, enabled }));
              }}
            />
            <FieldLabel htmlFor={`${id}-enabled`}>
              Search automatically
            </FieldLabel>
          </Field>

          <FieldSet className="gap-1.5" disabled={!draft.enabled}>
            <FieldLegend variant="label" className="mb-1.5">
              Days
            </FieldLegend>
            <ToggleGroup
              type="multiple"
              variant="outline"
              size="sm"
              spacing={1.5}
              aria-label="Days"
              value={draft.days}
              onValueChange={setDays}
              disabled={!draft.enabled}
              className="flex-wrap"
            >
              {WEEKDAYS.map((day) => (
                <ToggleGroupItem
                  key={day}
                  value={day}
                  aria-label={day.charAt(0).toUpperCase() + day.slice(1)}
                  // While the schedule is off its days are kept but muted: nothing runs on them.
                  data-muted={draft.enabled ? undefined : true}
                  className="data-muted:data-[state=on]:bg-secondary data-muted:data-[state=on]:text-secondary-foreground"
                >
                  {WEEKDAY_LABELS[day]}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            {/* No alert: the line under the form says it too, politely. */}
            {!draft.days.length ? (
              <FieldError role={undefined} className="text-xs">
                Pick at least one day.
              </FieldError>
            ) : null}
          </FieldSet>

          <FieldSet className="gap-1.5" disabled={!draft.enabled}>
            <FieldLegend variant="label" className="mb-1.5">
              Times
            </FieldLegend>
            <div className="flex flex-wrap items-center gap-2">
              {draft.times.map((time, index) => (
                // Wide enough for a 12 hour clock ("06:17 AM"), its picker icon and the remove button.
                <InputGroup key={index} className="w-40">
                  <InputGroupInput
                    type="time"
                    aria-label={`Time ${index + 1}`}
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
                    <InputGroupAddon align="inline-end">
                      <InputGroupButton
                        size="icon-xs"
                        aria-label={`Remove time ${index + 1}`}
                        onClick={() => {
                          setDraft((old) => ({
                            ...old,
                            times: old.times.filter((_, i) => i !== index),
                          }));
                        }}
                      >
                        <X aria-hidden="true" />
                      </InputGroupButton>
                    </InputGroupAddon>
                  ) : null}
                </InputGroup>
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
              <FieldError role={undefined} className="text-xs">
                Every time needs hours and minutes.
              </FieldError>
            ) : null}
          </FieldSet>

          {search ? (
            <p className="text-sm text-muted-foreground">
              The times are in your time zone, {draft.timezone}, which all your
              searches share: change it in the schedule of Main search. When two
              of your searches are due at the same time, the second waits in
              line and starts when the first ends.
            </p>
          ) : (
            <Field className="gap-1.5">
              <FieldLabel htmlFor={`${id}-zone`}>Time zone</FieldLabel>
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
            </Field>
          )}

          <p role="status" aria-live="polite" className="text-sm">
            {!draft.enabled
              ? "Automatic search is off."
              : next
                ? `Next automatic search: ${formatInZone(next, draft.timezone)}.`
                : "Pick at least one day and one time."}
          </p>
          {draft.enabled && shown && !shown.active && shown.enabled ? (
            <p className="text-xs text-muted-foreground">
              {search
                ? "This search has no job website, company board or job feed to search yet: nothing runs until it has one."
                : "No job website is ticked in Find jobs yet: nothing runs until one is."}
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
            disabled={saving || gone || (draft.enabled && invalid)}
          >
            {saving ? "Saving" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
