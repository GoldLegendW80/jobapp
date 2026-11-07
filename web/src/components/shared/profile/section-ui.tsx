/**
 * Building blocks shared by the profile tabs: text and date fields bound to react-hook-form,
 * the up, down and remove buttons of a list item, the Save bar, and the conflict dialog.
 */
import { ArrowDown, ArrowUp, CircleAlert, Save, Trash2 } from "lucide-react";
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
import { ButtonGroup } from "@/components/ui/button-group";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { describePeriod, typedPeriod } from "@/features/profile/helpers/period";

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

/**
 * The start or the end of an employer or of a school: a month, or a year alone when that is all
 * the user knows. One text field, the two forms as its placeholder, and under it how the app
 * reads what is typed ("Read as March 2021", "Read as the year 2021"), which a screen reader says
 * with the field. What is typed is what is saved: a year alone never gets a month.
 */
export function PeriodField<F extends FieldValues>({
  control,
  name,
  label,
  srSuffix,
  className,
  disabled,
}: Omit<FieldProps<F>, "description"> & { disabled?: boolean }) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => {
        const typed = typeof field.value === "string" ? field.value : "";
        return (
          <FormItem className={className}>
            <FormLabel>
              <LabelText label={label} srSuffix={srSuffix} />
            </FormLabel>
            <FormControl>
              <Input
                {...field}
                value={typed}
                disabled={disabled}
                placeholder="YYYY or YYYY-MM"
                inputMode="numeric"
                autoComplete="off"
                maxLength={7}
                onChange={(event) => {
                  field.onChange(typedPeriod(typed, event.target.value));
                }}
              />
            </FormControl>
            {disabled ? null : (
              <FormDescription>{describePeriod(typed)}</FormDescription>
            )}
            <FormMessage />
          </FormItem>
        );
      }}
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
  textareaClassName,
}: FieldProps<F> & { rows?: number; textareaClassName?: string }) {
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
            <Textarea
              rows={rows}
              className={textareaClassName}
              {...field}
              value={field.value ?? ""}
            />
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

/**
 * Move up, move down and remove (without `onRemove`, no remove), each named after the item for
 * screen readers. `id` names the buttons (`<id>-up`, `<id>-down`), for focus to follow a move.
 */
export function ItemControls({
  index,
  count,
  name,
  onMove,
  onRemove,
  disabled = false,
  id,
}: {
  index: number;
  count: number;
  name: string;
  onMove: (from: number, to: number) => void;
  onRemove?: (index: number) => void;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <ButtonGroup aria-label={`Order of ${name}`}>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        id={id ? `${id}-up` : undefined}
        aria-label={`Move ${name} up`}
        disabled={disabled || index === 0}
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
        id={id ? `${id}-down` : undefined}
        aria-label={`Move ${name} down`}
        disabled={disabled || index === count - 1}
        onClick={() => {
          onMove(index, index + 1);
        }}
      >
        <ArrowDown aria-hidden="true" />
      </Button>
      {onRemove ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Remove ${name}`}
          disabled={disabled}
          onClick={() => {
            onRemove(index);
          }}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      ) : null}
    </ButtonGroup>
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
  note,
}: {
  label: string;
  saving: boolean;
  dirty: boolean;
  /** A line beside the button: what the save will start, or what the last one did. */
  note?: ReactNode;
}) {
  return (
    <div className="sticky bottom-(--bottom-bar,0px) z-10 -mx-4 flex flex-wrap items-center justify-end gap-x-3 gap-y-2 border-t bg-card/95 px-4 py-3 shadow-md backdrop-blur sm:mx-0 sm:rounded-xl sm:border-t-0">
      {note ? (
        <div className="min-w-0 basis-full text-sm empty:hidden sm:flex-1 sm:basis-auto">
          {note}
        </div>
      ) : null}
      <span
        className={cn("text-sm text-muted-foreground", !dirty && "sr-only")}
        aria-live="polite"
      >
        {dirty ? "Unsaved changes" : "All changes saved"}
      </span>
      <Button type="submit" disabled={saving} aria-busy={saving}>
        {saving ? <Spinner aria-hidden="true" /> : <Save aria-hidden="true" />}
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

/** The items of a Group (employers, schools, languages): told apart by space and a hairline. */
export const ITEM_LIST = "flex flex-col divide-y";
export const ITEM = "py-5 first:pt-0 last:pb-0";

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
    <section>
      <Card className="gap-4 py-4 sm:py-6">
        {/* On a phone the action goes under the description, which keeps the full width. */}
        <CardHeader className="gap-1 px-4 max-sm:has-data-[slot=card-action]:grid-cols-1 sm:px-6">
          <CardTitle className="text-base">
            <h3>{title}</h3>
          </CardTitle>
          {description ? (
            <CardDescription>{description}</CardDescription>
          ) : null}
          {action ? (
            <CardAction className="max-sm:col-start-1 max-sm:row-span-1 max-sm:row-start-auto max-sm:mt-1 max-sm:justify-self-start">
              {action}
            </CardAction>
          ) : null}
        </CardHeader>
        <CardContent className="flex flex-col gap-4 px-4 sm:px-6">
          {children}
        </CardContent>
      </Card>
    </section>
  );
}
