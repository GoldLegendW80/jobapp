/**
 * Step 3, first part: choosing the documents and starting a run (the estimate, the consent the
 * first time, the reasons a start is refused). A placeholder until task G11 builds it in this
 * file; it reads the documents from the query cache (`documentsKey` in documents-api.ts) and
 * needs nothing from the step around it.
 */
export function RunStart() {
  return (
    <p className="text-sm text-muted-foreground">
      Soon, you will choose here which documents the agent reads. Before it
      starts, the app will show what is sent, to whom, and ask you first.
    </p>
  );
}
