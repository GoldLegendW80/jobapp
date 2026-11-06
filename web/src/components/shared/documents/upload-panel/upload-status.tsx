import { Spinner } from "@/components/ui/spinner";

/** A line of the upload that is still under way, with a spinner. */
export function UploadStatus({ children }: { children: string }) {
  return (
    <p
      role="status"
      className="flex items-center gap-2 text-sm text-muted-foreground"
    >
      <Spinner aria-hidden="true" role={undefined} />
      {children}
    </p>
  );
}
