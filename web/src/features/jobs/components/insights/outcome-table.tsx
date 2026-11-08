import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { Group } from "../../api/insights-api";
import { rateText } from "../../helpers/insights";

const NUMBER = "tabular-nums sm:text-right";

/**
 * Applications of each group (a fit band, an applicant tracking system, a source), the answers
 * and interviews they got, and the interview rate, or "Too few to say" under the api's floor.
 * A card per group on a phone.
 */
export function OutcomeTable({
  caption,
  groupName,
  groups,
  label,
}: {
  caption: string;
  groupName: string;
  groups: Group[];
  label: (key: string) => string;
}) {
  return (
    <Table>
      <TableCaption className="sr-only">{caption}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>{groupName}</TableHead>
          <TableHead className="text-right">Applications</TableHead>
          <TableHead className="text-right">Answered</TableHead>
          <TableHead className="text-right">Interviews</TableHead>
          <TableHead className="text-right">Interview rate</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.map((group) => (
          <TableRow key={group.key}>
            <TableHead className="font-normal">{label(group.key)}</TableHead>
            <TableCell label="Applications" className={NUMBER}>
              {formatCount(group.applied)}
            </TableCell>
            <TableCell label="Answered" className={NUMBER}>
              {formatCount(group.answered)}
            </TableCell>
            <TableCell label="Interviews" className={NUMBER}>
              {formatCount(group.interviews)}
            </TableCell>
            <TableCell
              label="Interview rate"
              className={cn(
                NUMBER,
                group.rate === null && "text-muted-foreground",
              )}
            >
              {rateText(group.rate)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
