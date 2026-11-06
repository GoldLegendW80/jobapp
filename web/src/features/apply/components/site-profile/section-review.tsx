import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCaption,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { describeError, errorDetail } from "@/lib/api";
import { siteProfileProblem } from "@/lib/wording";

import type { ProfileRun, ProfileSection } from "../../api/site-profile-api";
import { useSectionReview } from "@/features/apply/api/use-section-review";
import { ConfirmDialog } from "@/components/shared/apply/confirm-dialog";
import { ProfileFieldRow } from "./profile-field-row";
import { SectionScreenshots } from "./section-screenshots";

const COLUMNS = [
  "Field",
  "Value on the site",
  "Profile value or your edit",
  "Source",
  "Replace",
];

interface SectionReviewProps {
  run: ProfileRun;
  section: ProfileSection;
  update: (run: ProfileRun) => void | Promise<void>;
  refresh: () => void;
}

/**
 * One section of a site profile run to review: its screenshots, its fields with their sources and
 * the person's edits, then Approve section (behind a confirmation) or Skip section. A new run or
 * section starts with no draft.
 */
export function SectionReview(props: SectionReviewProps) {
  return (
    <SectionReviewContent
      key={`${props.run.id}:${props.section.section}`}
      {...props}
    />
  );
}

function SectionReviewContent(props: SectionReviewProps) {
  const { run, section } = props;
  const review = useSectionReview(props);
  const { save, editable, editPending } = review;
  return (
    <Card aria-labelledby={`profile-section-${section.section}`}>
      <CardHeader>
        <CardTitle>
          <h2 id={`profile-section-${section.section}`} className="text-lg">
            {section.label}
          </h2>
        </CardTitle>
        <Badge variant="secondary" role="status" className="capitalize">
          {section.state}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {section.events.some((event) => event.kind === "page_addressed") && (
          <p className="text-sm text-muted-foreground">
            This page addressed an AI assistant. Those instructions were
            ignored.
          </p>
        )}
        {section.error_code && (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              {siteProfileProblem(section.error_code)}
            </AlertDescription>
          </Alert>
        )}
        <SectionScreenshots run={run} section={section} />
        <Table>
          <TableCaption className="p-3">
            Fields and their sources. Existing site values stay until you select
            Replace.
          </TableCaption>
          <TableHeader>
            <TableRow>
              {COLUMNS.map((label) => (
                <TableHead className="p-3" key={label}>
                  {label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {section.fields.map((field) => (
              <ProfileFieldRow
                key={field.field_key}
                field={field}
                editable={editable && !run.autosave}
                value={review.fieldValue(field)}
                replace={review.replaceOf(field)}
                pending={editPending || save.isPending}
                error={review.errorOf(field)}
                onChange={(value) => {
                  review.changeField(field, value);
                }}
                onSave={() => {
                  review.keepEdit(field);
                }}
                onUndo={() => {
                  review.undoEdit(field);
                }}
                onReplace={(replace) => {
                  review.replaceField(field, replace);
                }}
              />
            ))}
          </TableBody>
        </Table>
        {save.error && (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              {errorDetail(save.error) ?? describeError(save.error)} The section
              has been refreshed. Review the current values before approving
              again.
            </AlertDescription>
          </Alert>
        )}
        {editable && (
          <div className="space-y-2">
            {review.unsaved && (
              <p role="status" className="text-sm text-muted-foreground">
                Keep or undo your edits before approving this section.
              </p>
            )}
            {editPending && (
              <p role="status" className="text-sm text-muted-foreground">
                Waiting for your field changes to be saved.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={save.isPending || review.approvalBlocked}
                onClick={review.askApprove}
              >
                Approve section
              </Button>
              <Button
                variant="outline"
                disabled={save.isPending || editPending}
                onClick={review.skip}
              >
                Skip section
              </Button>
            </div>
          </div>
        )}
        <ConfirmDialog
          ask={
            review.confirming
              ? {
                  title: `Save ${section.label}?`,
                  sentence: `Save the selected fields in ${section.label} on ${run.host}? Values on the site are replaced only where you chose Replace.`,
                  confirm: "Approve and save section",
                }
              : null
          }
          pending={save.isPending}
          onCancel={review.closeConfirm}
          onConfirm={review.approve}
        />
      </CardContent>
    </Card>
  );
}
