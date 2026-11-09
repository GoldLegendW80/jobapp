import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import { failureSentence } from "@/features/documents/api/documents-api";
import { usePrepare } from "@/features/documents/api/use-interview-prep";

/** Prepare (or Prepare again) on the person's click; a refusal is said under it. */
export function PrepareButton({
  jobId,
  label,
  variant = "default",
}: {
  jobId: string;
  label: string;
  variant?: "default" | "outline";
}) {
  const prepare = usePrepare(jobId);
  return (
    <div className="flex flex-col gap-3">
      <Button
        variant={variant}
        size="sm"
        className="self-start"
        disabled={prepare.isPending}
        onClick={() => {
          prepare.mutate();
        }}
      >
        {label}
      </Button>
      {prepare.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{failureSentence(prepare.error)}</AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
