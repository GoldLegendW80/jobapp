import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { useCloseSilent, type SilentApplication } from "../../api/insights-api";
import { daysText } from "../../helpers/insights";
import { plural } from "../../helpers/selection";

function line(row: SilentApplication): string {
  const applied = row.applied_on
    ? `Applied ${formatDate(row.applied_on)}. `
    : "";
  return `${applied}Last change ${formatDate(row.last_signal)}, ${daysText(row.days)} ago.`;
}

/**
 * The applications nobody answered for `days` days or more, each listed and ticked, and one
 * click that moves the ticked ones to Closed (never by itself). The api closes only those still
 * silent when the click lands, and the toast offers the bulk change's Undo.
 */
export function SilentSection({
  rows,
  days,
  undated,
}: {
  rows: SilentApplication[];
  days: number;
  undated: number;
}) {
  const [unticked, setUnticked] = useState<ReadonlySet<string>>(new Set());
  const { close, undo } = useCloseSilent();
  const ticked = rows
    .filter((row) => !unticked.has(row.id))
    .map((row) => row.id);

  const toggle = (id: string, on: boolean) => {
    setUnticked((now) => {
      const next = new Set(now);
      if (on) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const undoClose = (token: string) => {
    undo.mutate(token, {
      onSuccess: (result) => {
        toast.success(
          `Undone: ${plural(result.restored.length, "application")} back to Applied.`,
        );
      },
      onError: (error) => {
        toast.error(`The undo failed. ${describeError(error)}`);
      },
    });
  };
  const closeTicked = () => {
    close.mutate(ticked, {
      onSuccess: (result) => {
        const left = result.skipped.length
          ? ` ${plural(result.skipped.length, "application")} changed since and stayed open.`
          : "";
        const message = `Closed ${plural(result.closed.length, "application")}.${left}`;
        const token = result.undo_token;
        if (token)
          toast.success(message, {
            duration: 15_000,
            action: {
              label: "Undo",
              onClick: () => {
                undoClose(token);
              },
            },
          });
        else toast.message(message);
      },
      onError: (error) => {
        toast.error(`Nothing was closed. ${describeError(error)}`);
      },
    });
  };

  return (
    <Section
      title="Silent applications"
      description={`Still marked Applied, with no answer, no status change, no note and no click for ${daysText(days)} or more. Closing moves the ticked ones to Closed, only when you click.`}
      actions={
        rows.length ? (
          <Button
            onClick={closeTicked}
            disabled={ticked.length === 0 || close.isPending}
            aria-busy={close.isPending}
          >
            {close.isPending ? (
              <Spinner aria-hidden="true" role={undefined} />
            ) : null}
            Close {plural(ticked.length, "application")}
          </Button>
        ) : null
      }
    >
      <Card>
        <CardContent className="flex flex-col gap-3">
          {rows.length ? (
            <ul
              className="flex flex-col divide-y"
              aria-label="Silent applications"
            >
              {rows.map((row) => {
                const id = `silent-${row.id}`;
                return (
                  <li
                    key={row.id}
                    className="flex items-start gap-3 py-3 first:pt-0 last:pb-0"
                  >
                    <Checkbox
                      id={id}
                      checked={!unticked.has(row.id)}
                      onCheckedChange={(on) => {
                        toggle(row.id, on === true);
                      }}
                      className="mt-0.5"
                    />
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <Label htmlFor={id} className="font-medium">
                        {row.title} at {row.company}
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        {line(row)}{" "}
                        <Button variant="link" asChild className="h-auto p-0">
                          <Link
                            to={`/jobs/${row.id}`}
                            aria-label={`Open ${row.title} at ${row.company}`}
                          >
                            Open
                          </Link>
                        </Button>
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No application has been silent that long.
            </p>
          )}
          {undated ? (
            <p className="text-sm text-muted-foreground">
              {plural(undated, "application")} marked Applied{" "}
              {undated === 1 ? "has" : "have"} no date at all, so{" "}
              {undated === 1 ? "it is" : "they are"} not listed.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}
