import { zodResolver } from "@hookform/resolvers/zod";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

const schema = z.object({
  title: z
    .string()
    .trim()
    .min(2, "Write the job title you look for, like product designer."),
  salary: z
    .string()
    .regex(/^\d*$/, "Write the salary as a whole number, like 52000."),
  notice: z.enum(["0", "1", "2", "3"]),
  remoteOnly: z.boolean(),
  pitch: z
    .string()
    .max(
      300,
      "Keep it under 300 characters: the first lines are what is read.",
    ),
});

type Values = z.infer<typeof schema>;

const EMPTY: Values = {
  title: "",
  salary: "",
  notice: "2",
  remoteOnly: false,
  pitch: "",
};

/**
 * The search settings form, built with Form: react-hook-form holds the values, the zod schema
 * writes the error messages, and FormMessage shows each under its control.
 */
function SearchSettingsForm({
  defaultValues,
  validateOnMount = false,
}: {
  defaultValues?: Partial<Values>;
  validateOnMount?: boolean;
}) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { ...EMPTY, ...defaultValues },
  });

  useEffect(() => {
    if (validateOnMount) void form.trigger();
  }, [form, validateOnMount]);

  return (
    <Form {...form}>
      <form
        noValidate
        aria-label="Search settings"
        className="flex max-w-md flex-col gap-6"
        onSubmit={(event) =>
          void form.handleSubmit(async () => {
            await new Promise((resolve) => setTimeout(resolve, 800));
            toast.success("Search settings saved");
          })(event)
        }
      >
        <FormField
          control={form.control}
          name="title"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Job title</FormLabel>
              <FormControl>
                <Input placeholder="For example: product designer" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="salary"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Desired salary, in euros a year</FormLabel>
              <FormControl>
                <Input inputMode="numeric" className="max-w-48" {...field} />
              </FormControl>
              <FormDescription>
                Postings that pay less score lower. Leave it empty if you would
                rather not say.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="notice"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Notice period</FormLabel>
              <FormControl>
                <NativeSelect {...field}>
                  <option value="0">None, I can start now</option>
                  <option value="1">1 month</option>
                  <option value="2">2 months</option>
                  <option value="3">3 months</option>
                </NativeSelect>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="remoteOnly"
          render={({ field }) => (
            <FormItem className="flex items-start justify-between gap-4">
              <div className="flex flex-col gap-1.5">
                <FormLabel>Remote only</FormLabel>
                <FormDescription>
                  Only postings you can do from home reach your deck.
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  name={field.name}
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  onBlur={field.onBlur}
                />
              </FormControl>
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="pitch"
          render={({ field }) => (
            <FormItem>
              <FormLabel>What you want to do next</FormLabel>
              <FormControl>
                <Textarea
                  placeholder="For example: design tools for small logistics companies"
                  {...field}
                />
              </FormControl>
              <FormDescription>
                The assistant reads it when it scores a posting.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button
          type="submit"
          className="self-start"
          disabled={form.formState.isSubmitting}
        >
          {form.formState.isSubmitting ? <Spinner /> : null}
          {form.formState.isSubmitting ? "Saving" : "Save search settings"}
        </Button>
      </form>
    </Form>
  );
}

const meta = {
  title: "Components/Forms/Form",
  component: SearchSettingsForm,
  subcomponents: {
    FormField,
    FormItem,
    FormLabel,
    FormControl,
    FormDescription,
    FormMessage,
  },
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A form whose values react-hook-form holds and a zod schema checks: the schema writes each error once, FormMessage shows it under its control, and the first invalid field takes the focus on submit. A form with a single field and no rules can use Field alone.",
      },
    },
  },
} satisfies Meta<typeof SearchSettingsForm>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The search settings of a profile. Save shows a Spinner and disables itself while it saves, then a toast says it is done. */
export const Typical: Story = {
  args: {
    defaultValues: {
      title: "Product designer",
      salary: "52000",
      remoteOnly: true,
    },
  },
};

/** Nothing typed yet: the placeholders show the expected shape, and Save checks everything at once. */
export const Empty: Story = {};

/** Each error is a sentence from the schema, under its control; the label turns red with it. */
export const Error: Story = {
  args: {
    defaultValues: {
      title: "",
      salary: "52k",
      pitch:
        "I want to design tools for small logistics companies, where one screen saves a dispatcher an hour a day. I have done it for four years at a carrier in Nantes and I would like to do it for a product used by many carriers, with a team that tests its work with the people who use it every day, from the warehouse to the office.",
    },
    validateOnMount: true,
  },
};
