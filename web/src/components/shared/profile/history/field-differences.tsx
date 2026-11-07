import { useId, useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

import type { SectionName } from "@/features/profile/api/profile-api";
import {
  diffFields,
  flattenSection,
  type FieldDiff,
} from "@/features/profile/helpers/diff";

const STATUS_TEXT: Record<Exclude<FieldDiff["status"], "same">, string> = {
  changed: "Changed",
  added: "Only in this version",
  removed: "Only in the current version",
};

/** A section of a revision against the current one, field by field, in a table. */
export function FieldDifferences({
  section,
  revision,
  before,
  current,
}: {
  section: SectionName;
  revision: number;
  before: unknown;
  current: unknown;
}) {
  const id = useId();
  const [onlyChanges, setOnlyChanges] = useState(false);
  const rows = diffFields(
    flattenSection(section, before),
    flattenSection(section, current),
  );
  const changed = rows.filter((row) => row.status !== "same").length;
  const shown = onlyChanges
    ? rows.filter((row) => row.status !== "same")
    : rows;
  return (
    <section aria-label="Differences" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {changed === 0
            ? "No difference with the current version."
            : `${String(changed)} ${changed === 1 ? "field differs" : "fields differ"} from the current version.`}
        </p>
        <Label htmlFor={id} className="font-normal">
          <Checkbox
            id={id}
            checked={onlyChanges}
            onCheckedChange={(checked) => {
              setOnlyChanges(checked === true);
            }}
          />
          Only differences
        </Label>
      </div>
      <Table
        stacked={false}
        aria-label={`Revision ${String(revision)} and the current version`}
        className="table-fixed border-collapse text-xs sm:text-sm"
      >
        <TableHeader>
          <TableRow>
            <TableHead className="w-[34%]">Field</TableHead>
            <TableHead>Revision {revision}</TableHead>
            <TableHead>Current</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((row) => (
            <TableRow
              key={row.path}
              data-status={row.status}
              className={cn(
                "align-top",
                row.status !== "same" &&
                  "bg-warning-muted hover:bg-warning-muted",
              )}
            >
              <TableHead className="font-normal [overflow-wrap:anywhere]">
                <span className="font-medium">{row.label}</span>
                {row.status !== "same" ? (
                  <span className="mt-1 block text-xs font-medium text-warning">
                    {STATUS_TEXT[row.status]}
                  </span>
                ) : null}
              </TableHead>
              <TableCell className="whitespace-pre-wrap [overflow-wrap:anywhere]">
                <FieldValue value={row.revision} />
              </TableCell>
              <TableCell className="whitespace-pre-wrap [overflow-wrap:anywhere]">
                <FieldValue value={row.current} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}

function FieldValue({ value }: { value: string | undefined }) {
  if (value === undefined || value === "")
    return <span className="text-muted-foreground">Empty</span>;
  return <>{value}</>;
}
