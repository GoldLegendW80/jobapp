/**
 * Building blocks shared by the profile tabs: text and month fields bound to react-hook-form,
 * the up, down and remove buttons of a list item, the Save bar, and the conflict dialog.
 */
import {
  ArrowDown,
  ArrowUp,
  CircleAlert,
  LoaderCircle,
  Save,
  Trash2,
} from "lucide-react";
import type { ReactNode } from "react";
import type { Control, FieldPath, FieldValues } from "react-hook-form";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface FieldProps<F extends FieldValues> {
  control: Control<F>;
  name: FieldPath<F>;
  label: string;
  /** read by screen readers after the label, to tell list items apart ("of employer 2") */
  srSuffix?: string;
  description?: ReactNode;
  className?: string;
}

export function LabelText({
  label,
  srSuffix,
}: {
  label: string;
  srSuffix?: string;
}) {
  return (
    <>
      {label}
      {srSuffix ? <span className="sr-only"> {srSuffix}</span> : null}
    </>
  );
}

export function TextField<F extends FieldValues>({
  control,
  name,
  label,
  srSuffix,
  description,
  className,
  ...input
}: FieldProps<F> & Omit<React.ComponentProps<"input">, "name">) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className={className}>
          <FormLabel>
            <LabelText label={label} srSuffix={srSuffix} />
          </FormLabel>
          <FormControl>
            <Input {...input} {...field} value={field.value ?? ""} />
          </FormControl>
          {description ? (
            <FormDescription>{description}</FormDescription>
          ) : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

export function MonthField<F extends FieldValues>(
  props: FieldProps<F> & { disabled?: boolean },
) {
  return (
    <TextField
      {...props}
      placeholder="YYYY-MM"
      inputMode="numeric"
      autoComplete="off"
      maxLength={7}
    />
  );
}

export function AreaField<F extends FieldValues>({
  control,
  name,
  label,
  srSuffix,
  description,
  className,
  rows = 3,
}: FieldProps<F> & { rows?: number }) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className={className}>
          <FormLabel>
            <LabelText label={label} srSuffix={srSuffix} />
          </FormLabel>
          <FormControl>
            <Textarea rows={rows} {...field} value={field.value ?? ""} />
          </FormControl>
          {description ? (
            <FormDescription>{description}</FormDescription>
          ) : null}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Move up, move down and remove, each named after the item for screen readers. */
export function ItemControls({
  index,
  count,
  name,
  onMove,
  onRemove,
}: {
  index: number;
  count: number;
  name: string;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Move ${name} up`}
        disabled={index === 0}
        onClick={() => {
          onMove(index, index - 1);
        }}
      >
        <ArrowUp aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Move ${name} down`}
        disabled={index === count - 1}
        onClick={() => {
          onMove(index, index + 1);
        }}
      >
        <ArrowDown aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Remove ${name}`}
        onClick={() => {
          onRemove(index);
        }}
      >
        <Trash2 aria-hidden="true" />
      </Button>
    </div>
  );
}

export function FormErrorAlert({ message }: { message: string | undefined }) {
  if (!message) return null;
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

/** Sticky at the bottom of the tab, so Save is reachable on a phone without scrolling back. */
export function SaveBar({
  label,
  saving,
  dirty,
}: {
  label: string;
  saving: boolean;
  dirty: boolean;
}) {
  return (
    <div className="sticky bottom-0 z-10 -mx-4 flex items-center justify-end gap-3 border-t bg-background/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-b-lg">
      <span
        className={cn("text-sm text-muted-foreground", !dirty && "sr-only")}
        aria-live="polite"
      >
        {dirty ? "Unsaved changes" : "All changes saved"}
      </span>
      <Button type="submit" disabled={saving} aria-busy={saving}>
        {saving ? (
          <LoaderCircle aria-hidden="true" className="animate-spin" />
        ) : (
          <Save aria-hidden="true" />
        )}
        {saving ? "Saving" : `Save ${label.toLowerCase()}`}
      </Button>
    </div>
  );
}

export function ConflictDialog({
  open,
  label,
  onKeepEditing,
  onReload,
}: {
  open: boolean;
  label: string;
  onKeepEditing: () => void;
  onReload: () => void;
}) {
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onKeepEditing();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>This section changed elsewhere</AlertDialogTitle>
          <AlertDialogDescription>
            {label} was saved from another tab or device after you opened it.
            Reload it to see that version and lose your changes here, or keep
            editing and save again to replace it with yours.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <Button variant="outline" onClick={onKeepEditing}>
            Keep editing
          </Button>
          <Button onClick={onReload}>Reload (discard mine)</Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** A titled group of fields inside a tab. */
export function Group({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 rounded-lg border p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-semibold">{title}</h3>
          {description ? (
            <p className="text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
