import { CircleAlert } from "lucide-react";

import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

/** A section's state in a badge of its meaning; the others are plain. */
const STATE_BADGE: Record<string, "success" | "info" | "destructive"> = {
  ready: "info",
  saved: "success",
  failed: "destructive",
};

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
 * One section of a site profile run to review: its fields with their sources and the person's
 * edits in one table, with Approve section (behind a confirmation) and Skip section as the
 * section's actions. Its screenshots are in the page's side rail. A new run or section starts
 * with no draft.
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
    <Section
      title={section.label}
      description={
        <Badge
          variant={STATE_BADGE[section.state] ?? "outline"}
          role="status"
          className="capitalize"
        >
          {section.state}
        </Badge>
      }
      actions={
        editable ? (
          <>
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
          </>
        ) : null
      }
    >
      {section.events.some((event) => event.kind === "page_addressed") && (
        <p className="text-sm text-muted-foreground">
          This page addressed an AI assistant. Those instructions were ignored.
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
      {editable && review.unsaved && (
        <p role="status" className="text-sm text-muted-foreground">
          Keep or undo your edits before approving this section.
        </p>
      )}
      {editable && editPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Waiting for your field changes to be saved.
        </p>
      )}
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
    </Section>
  );
}
