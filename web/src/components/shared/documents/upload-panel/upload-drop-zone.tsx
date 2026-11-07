import { FileUp } from "lucide-react";
import { useId, useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import { CV_FILE_TYPES } from "@/features/documents/helpers/upload-cv";

/** Where a CV is dropped, or chosen with "Choose a file". */
export function UploadDropZone({
  busy,
  onFile,
}: {
  busy: boolean;
  onFile: (file: File | undefined) => void;
}) {
  const hintId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      data-over={over}
      data-testid="upload-drop"
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          setOver(false);
      }}
      onDrop={(event: DragEvent<HTMLDivElement>) => {
        event.preventDefault();
        setOver(false);
        onFile(event.dataTransfer.files[0]);
      }}
      className={cn(
        "flex flex-col items-start gap-3 rounded-md border border-dashed p-4 transition-colors",
        over && "border-primary bg-accent",
      )}
    >
      <p id={hintId} className="text-sm text-muted-foreground">
        {WORDING.uploadCvHint}
      </p>
      <Input
        ref={input}
        type="file"
        accept={CV_FILE_TYPES}
        hidden
        data-testid="upload-cv-file"
        onChange={(event) => {
          onFile(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <Button
        type="button"
        variant="outline"
        disabled={busy}
        aria-describedby={hintId}
        onClick={() => input.current?.click()}
      >
        <FileUp aria-hidden="true" />
        Choose a file
      </Button>
    </div>
  );
}
