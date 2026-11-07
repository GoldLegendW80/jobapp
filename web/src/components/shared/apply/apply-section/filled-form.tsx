import { Alert, AlertDescription } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";

import type {
  Answer,
  Application,
  Approval,
} from "@/features/apply/api/apply-api";
import { documentLine, lastShot } from "@/features/apply/helpers/apply-state";
import { FieldsList } from "../fields-list";
import { ShotView } from "../shot-view";

interface FormProps {
  application: Application;
  editable: boolean;
  saving: boolean;
  onSave: (answer: Answer, value: string | boolean) => Promise<boolean>;
  onDirtyChange: (count: number) => void;
}

/**
 * The filled form of an application: its answers, the documents sent with it and the screenshots
 * before and after the click, once read; else why it could not be read, or that it is loading.
 */
export function FilledForm({
  approval,
  loadError,
  ...form
}: FormProps & {
  approval: Approval | undefined;
  loadError: Error | null;
}) {
  if (approval) return <FormPanel approval={approval} {...form} />;
  if (loadError)
    return (
      <Alert variant="destructive">
        <AlertDescription>
          The filled form could not be loaded. {describeError(loadError)}
        </AlertDescription>
      </Alert>
    );
  return (
    <p
      role="status"
      className="flex items-center gap-2 text-sm text-muted-foreground"
    >
      <Spinner aria-hidden="true" role={undefined} />
      Loading the filled form
    </p>
  );
}

function FormPanel({
  application,
  approval,
  editable,
  saving,
  onSave,
  onDirtyChange,
}: FormProps & { approval: Approval }) {
  const before = lastShot(approval, "before");
  const after = lastShot(approval, "after");
  const documents = Object.entries(approval.documents);
  return (
    <div className="flex flex-col gap-4">
      {approval.answers.length > 0 ? (
        <FieldsList
          answers={approval.answers}
          editable={editable}
          saving={saving}
          onSave={onSave}
          onDirtyChange={onDirtyChange}
          applicationId={application.id}
        />
      ) : null}
      {documents.length > 0 ? (
        <section aria-label="Documents to send" className="flex flex-col gap-1">
          <h3 className="text-sm font-semibold">Documents sent with it</h3>
          <ul className="text-sm">
            {documents.map(([kind, one]) => (
              <li key={kind}>{documentLine(kind, one)}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {before || after ? (
        <div className="grid gap-4 @md:grid-cols-2">
          {before ? (
            <ShotView
              applicationId={application.id}
              shotId={before.id}
              caption="The form before the click"
            />
          ) : null}
          {after ? (
            <ShotView
              applicationId={application.id}
              shotId={after.id}
              caption="The page after the click"
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
