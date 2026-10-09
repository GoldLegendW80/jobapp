/**
 * Facts: the fact base as named Markdown blocks. Each block is collapsible; open, it shows its
 * title and a plain textarea with a live preview through the safe renderer (markdown.tsx), side
 * by side from the md breakpoint, behind a Write and Preview switch below it. Blocks can be
 * added, moved and removed; a meter shows the size against the api's 200 KB cap.
 */
import { ChevronRight, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useFieldArray, useWatch, type UseFormReturn } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { cn } from "@/lib/utils";

import type { Profile } from "./api";
import {
  FACT_BLOCKS_MAX,
  FACTS_MAX_BYTES,
  factBytes,
  factsSchema,
  fromFactsForm,
  sizeText,
  toFactsForm,
  type FactsForm,
} from "./editor-forms";
import { Markdown } from "./markdown";
import {
  AreaField,
  ConflictDialog,
  FormErrorAlert,
  Group,
  ItemControls,
  SaveBar,
  TextField,
} from "./section-ui";
import { useSectionForm } from "./use-section-form";

export function FactsTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "facts",
      profile,
      schema: factsSchema,
      toForm: toFactsForm,
      fromForm: fromFactsForm,
      rules: { unique: { blocks: "title" } },
      onDirtyChange,
    });
  const blocks = useFieldArray({ control: form.control, name: "blocks" });
  // Which blocks are open, by field id (stable across moves).
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const toggle = (id: string, value?: boolean) => {
    setOpen((old) => {
      const next = new Set(old);
      if (value ?? !next.has(id)) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  // A block just added opens.
  const openNext = useRef(false);
  const focusNext = useRef<number>(undefined);
  useEffect(() => {
    const last = blocks.fields[blocks.fields.length - 1];
    if (openNext.current && last) {
      openNext.current = false;
      focusNext.current = blocks.fields.length - 1;
      toggle(last.id, true);
    }
  }, [blocks.fields]);
  // ...and its title takes the focus once it is shown.
  useEffect(() => {
    if (focusNext.current === undefined) return;
    form.setFocus(`blocks.${focusNext.current}.title`);
    focusNext.current = undefined;
  }, [open, form]);

  // A block with an error stays open, so its message is visible.
  const blockErrors = form.formState.errors.blocks;
  const hasError = (index: number) =>
    Array.isArray(blockErrors) && blockErrors[index] !== undefined;

  const rootError =
    (blockErrors as { root?: { message?: string } } | undefined)?.root
      ?.message ?? (blockErrors as { message?: string } | undefined)?.message;

  return (
    <div className="flex flex-col gap-6">
      <Form {...form}>
        <form
          noValidate
          aria-label="Facts"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="Fact blocks"
            description="The only source of claims for generated documents. Name each block so agents can cite it. Markdown: headings, lists, bold, italic, links and code."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={blocks.fields.length >= FACT_BLOCKS_MAX}
                onClick={() => {
                  openNext.current = true;
                  blocks.append({ title: "", body: "" });
                }}
              >
                <Plus aria-hidden="true" />
                Add block
              </Button>
            }
          >
            <SizeMeter form={form} />
            {rootError ? (
              <p role="alert" className="text-sm text-destructive">
                {rootError}
              </p>
            ) : null}
            {blocks.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No fact block yet.
              </p>
            ) : (
              <ol className="flex flex-col gap-3">
                {blocks.fields.map((item, index) => (
                  <li key={item.id} className="rounded-md border">
                    <BlockEditor
                      form={form}
                      index={index}
                      count={blocks.fields.length}
                      open={open.has(item.id) || hasError(index)}
                      onToggle={(value) => {
                        toggle(item.id, value);
                      }}
                      onMove={blocks.move}
                      onRemove={blocks.remove}
                    />
                  </li>
                ))}
              </ol>
            )}
          </Group>
          <SaveBar
            label="Facts"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <ConflictDialog
        open={conflict}
        label="Facts"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
}

function SizeMeter({ form }: { form: UseFormReturn<FactsForm> }) {
  const blocks = useWatch({ control: form.control, name: "blocks" });
  const size = factBytes(blocks);
  const percent = Math.min(100, Math.round((100 * size) / FACTS_MAX_BYTES));
  const over = size > FACTS_MAX_BYTES;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span id="facts-size-label" className="font-medium">
          Fact base size
        </span>
        <span
          className={cn(
            "tabular-nums text-muted-foreground",
            over && "font-medium text-destructive",
          )}
        >
          {sizeText(size, FACTS_MAX_BYTES)}
        </span>
      </div>
      <div
        role="meter"
        aria-labelledby="facts-size-label"
        aria-valuemin={0}
        aria-valuemax={FACTS_MAX_BYTES}
        aria-valuenow={Math.min(size, FACTS_MAX_BYTES)}
        aria-valuetext={sizeText(size, FACTS_MAX_BYTES)}
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width]",
            over
              ? "bg-destructive"
              : percent >= 90
                ? "bg-amber-500"
                : "bg-primary",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function BlockEditor({
  form,
  index,
  count,
  open,
  onToggle,
  onMove,
  onRemove,
}: {
  form: UseFormReturn<FactsForm>;
  index: number;
  count: number;
  open: boolean;
  onToggle: (value?: boolean) => void;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  const n = index + 1;
  const title = useWatch({
    control: form.control,
    name: `blocks.${index}.title`,
  });
  const body = useWatch({
    control: form.control,
    name: `blocks.${index}.body`,
  });
  const [view, setView] = useState<"write" | "preview">("write");
  const panelId = `fact-block-${n}`;
  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between gap-2 p-2 pl-3">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={`${title.trim() || `Block ${n}`}, block ${n}`}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-sm text-left text-sm font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          onClick={() => {
            onToggle();
          }}
        >
          <ChevronRight
            aria-hidden="true"
            className={cn(
              "size-4 shrink-0 transition-transform",
              open && "rotate-90",
            )}
          />
          <span className="min-w-0 truncate">
            {title.trim() || `Block ${n}`}
          </span>
        </button>
        <ItemControls
          index={index}
          count={count}
          name={`block ${n}`}
          onMove={onMove}
          onRemove={onRemove}
        />
      </div>
      {open ? (
        <div id={panelId} className="flex flex-col gap-3 border-t p-3 sm:p-4">
          <TextField
            control={form.control}
            name={`blocks.${index}.title`}
            label="Title"
            srSuffix={`of block ${n}`}
          />
          <div
            role="group"
            aria-label={`Editor view of block ${n}`}
            className="flex gap-1 md:hidden"
          >
            {(["write", "preview"] as const).map((mode) => (
              <Button
                key={mode}
                type="button"
                size="sm"
                variant={view === mode ? "secondary" : "ghost"}
                aria-pressed={view === mode}
                onClick={() => {
                  setView(mode);
                }}
              >
                {mode === "write" ? "Write" : "Preview"}
              </Button>
            ))}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <AreaField
              control={form.control}
              name={`blocks.${index}.body`}
              label="Text"
              srSuffix={`of block ${n}`}
              rows={12}
              className={cn("min-w-0", view === "preview" && "max-md:hidden")}
              textareaClassName="font-mono text-xs leading-relaxed"
            />
            <section
              aria-label={`Preview of block ${n}`}
              className={cn(
                "min-w-0 rounded-md border bg-muted/30 p-3 md:max-h-96 md:overflow-y-auto",
                view === "write" && "max-md:hidden",
              )}
            >
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                Preview
              </p>
              {body.trim() === "" ? (
                <p className="text-sm text-muted-foreground">Nothing yet.</p>
              ) : (
                <Markdown source={body} />
              )}
            </section>
          </div>
        </div>
      ) : null}
    </div>
  );
}
