import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { profileKey } from "@/features/profile/api";
import { describeError, isApiError } from "@/lib/api";
import { WORDING } from "@/lib/wording";

import {
  chooseStarter,
  entrySource,
  getStarter,
  starterKey,
  type StarterList,
} from "./starter-api";

const SEARCH_OPTIONS = "/profile?tab=search";
function count(n: number): string {
  return n === 1 ? "1 board or site" : `${n} boards and sites`;
}

/**
 * The starter list of the wizard's summary (opportunities O5b): public company boards and the
 * Google Careers and VIE sites, proposed from the roles, industries and places the person gave.
 * Nothing is added until the person chooses: "Add all", or tick some and "Add the ticked ones".
 * "Remove" takes an entry off the list for good. What is added goes to Search options, enabled.
 */
export function StarterBoards() {
  const client = useQueryClient();
  const list = useQuery({
    queryKey: starterKey,
    queryFn: ({ signal }) => getStarter(signal),
  });
  const [ticked, setTicked] = useState<readonly string[]>([]);
  const [said, setSaid] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const id = useId();

  const choose = useMutation({
    mutationFn: chooseStarter,
    onSuccess: (answer, asked) => {
      client.setQueryData<StarterList>(starterKey, answer.list);
      setTicked((old) =>
        old.filter((key) =>
          answer.list.entries.some((e) => e.key === key && !e.added),
        ),
      );
      setProblem(null);
      if (answer.added.length) {
        // The boards are in the search section now: the summary's figures read it again.
        void client.invalidateQueries({ queryKey: profileKey });
        setSaid(
          `Added ${count(answer.added.length)} to ${WORDING.searchOptions}.`,
        );
      } else if (asked.remove?.length) {
        setSaid("Taken off the list.");
      } else {
        setSaid("Nothing was added: that list changed meanwhile.");
      }
    },
    onError: (error) => {
      setProblem(`That did not work. ${describeError(error)}`);
    },
  });

  if (list.isPending)
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Looking for boards that fit your answers.
      </p>
    );
  if (list.isError) {
    const unreadable =
      isApiError(list.error) &&
      list.error.kind === "http" &&
      list.error.status === 409;
    return (
      <Alert role="alert">
        <CircleAlert aria-hidden="true" />
        <AlertDescription className="flex flex-col items-start gap-2">
          {unreadable
            ? `Open ${WORDING.searchOptions} and save them again, then come back to this list.`
            : `The list of boards could not load. ${describeError(list.error)}`}
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void list.refetch();
            }}
          >
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  const entries = list.data.entries;
  const open = entries.filter((entry) => !entry.added);
  const busy = choose.isPending;
  const byAddress = (
    <p className="text-sm text-muted-foreground">
      You can add any other company board by its address in{" "}
      <Link
        to={SEARCH_OPTIONS}
        className="underline underline-offset-4 hover:text-foreground"
      >
        {WORDING.searchOptions}
      </Link>
      .
    </p>
  );

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex flex-col gap-3 rounded-md border p-3"
      data-testid="starter-boards"
    >
      <h3 id={`${id}-title`} className="text-base font-semibold">
        Boards and sites to search
      </h3>
      {problem ? (
        <Alert variant="destructive" role="alert">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{problem}</AlertDescription>
        </Alert>
      ) : null}
      {entries.length === 0 ? (
        <>
          <p className="text-sm">
            {list.data.targets
              ? "No starter board matches your answers."
              : "No starter board to propose yet."}
            {list.data.unmatched.length
              ? ` None is listed for ${list.data.unmatched.join(", ")}.`
              : ""}
          </p>
          {byAddress}
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            From your answers, these public company boards and sites may list
            postings for you. Nothing is added until you choose.
          </p>
          {entries.some((entry) => entry.kind === "board") ? null : (
            <p className="text-sm">
              {list.data.targets
                ? "No starter board matches your answers."
                : "No starter board to propose yet."}
              {list.data.unmatched.length
                ? ` None is listed for ${list.data.unmatched.join(", ")}.`
                : ""}
            </p>
          )}
          <ul className="flex flex-col divide-y">
            {entries.map((entry) => (
              <li
                key={entry.key}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
              >
                {entry.added ? (
                  <Badge variant="secondary">Added</Badge>
                ) : (
                  <input
                    id={`${id}-${entry.key}`}
                    type="checkbox"
                    className="size-4 accent-primary"
                    disabled={busy}
                    checked={ticked.includes(entry.key)}
                    onChange={(event) => {
                      const on = event.target.checked;
                      setTicked((old) =>
                        on
                          ? [...old.filter((k) => k !== entry.key), entry.key]
                          : old.filter((k) => k !== entry.key),
                      );
                    }}
                  />
                )}
                <label
                  htmlFor={entry.added ? undefined : `${id}-${entry.key}`}
                  className="flex min-w-0 flex-1 flex-col text-sm"
                >
                  <span className="font-medium break-words">
                    {entry.company}
                  </span>
                  <span className="break-words text-muted-foreground">
                    {entrySource(entry)}. {entry.why}
                  </span>
                </label>
                {entry.added ? null : (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    aria-label={`Remove ${entry.company} from the list`}
                    onClick={() => {
                      choose.mutate({ remove: [entry.key] });
                    }}
                  >
                    Remove
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={busy || open.length === 0}
              onClick={() => {
                choose.mutate({ add: open.map((entry) => entry.key) });
              }}
            >
              {busy ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : null}
              Add all
            </Button>
            <Button
              variant="outline"
              disabled={busy || ticked.length === 0}
              onClick={() => {
                choose.mutate({ add: [...ticked] });
              }}
            >
              Add the ticked ones
            </Button>
          </div>
          {byAddress}
        </>
      )}
      <p
        className="text-sm text-muted-foreground empty:hidden"
        role="status"
        aria-live="polite"
      >
        {said}
      </p>
    </section>
  );
}
