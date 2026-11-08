import { AdvancedSection } from "@/components/blocks/advanced-section";
import { CvConversion } from "@/components/shared/documents/upload-panel";
import { AssistantPrompt } from "@/components/shared/ingestion/assistant-prompt";
import { StepDocuments } from "@/components/shared/ingestion/step-documents";
import type { useCvUpload } from "@/features/documents/api/use-cv-upload";
import { WORDING } from "@/lib/wording";

/**
 * Step 2, Your profile: one place to add every file (the first PDF or Word file is the CV, or the
 * one marked with "This is my CV": it becomes the base CV with its look), and under Advanced the
 * analysis of the computer by Claude Code or Codex, whose answer is pasted with the rest. Step 3
 * reads them all into the profile. `cv` is the wizard's, so its conversion outlives the step.
 */
export function StepProfile({ cv }: { cv: ReturnType<typeof useCvUpload> }) {
  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-prose text-sm text-muted-foreground">
        Add your CV and any other file about your work. The first PDF or Word
        file you add becomes your CV in the app, with its look: the CVs the app
        writes for you keep it. Another file is your CV? Click This is my CV
        beside it. Turning it into a CV you can edit uses the Claude access
        saved in Model access: until one is saved, that part waits. The next
        step reads them all into your profile.
      </p>
      <StepDocuments flat autoCv onCv={cv.take} cvId={cv.uploadId} />
      <div className="flex flex-col gap-3 empty:hidden">
        <CvConversion
          upload={cv}
          openHref={(id) => `/profile/cv/${id}`}
          completeHere={false}
        />
      </div>
      <AdvancedSection title={WORDING.advanced} pageKey="welcome-analyze">
        <div className="flex flex-col gap-4 pt-4">
          <div className="flex flex-col gap-1">
            <h3 className="text-base font-semibold tracking-tight">
              {WORDING.analyzeYourComputer}
            </h3>
            <p className="max-w-prose text-sm text-muted-foreground">
              If you use Claude Code or Codex on your computer, it can look
              through it and answer with everything about your work. It sends
              what it reads to its own model provider, under your own account.
              This app never reads your computer: it only gets the answer you
              paste.
            </p>
          </div>
          <AssistantPrompt flat addIt="paste it above, in Or paste a text" />
        </div>
      </AdvancedSection>
    </div>
  );
}
