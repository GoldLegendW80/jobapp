import type { Meta, StoryObj } from "@storybook/react-vite";
import { useForm } from "react-hook-form";
import { expect, userEvent, within } from "storybook/test";

import { PasswordField } from "@/components/shared/auth/password-field";
import { Form } from "@/components/ui/form";
import { PASSWORD_HINT } from "@/features/auth/helpers/password-rules";

function PasswordForm({
  disabled,
  description,
}: {
  disabled?: boolean;
  description?: string;
}) {
  const form = useForm({ defaultValues: { password: "a long passphrase" } });
  return (
    <Form {...form}>
      <form className="w-80">
        <PasswordField
          control={form.control}
          name="password"
          label="Password"
          autoComplete="new-password"
          description={description}
          disabled={disabled}
        />
      </form>
    </Form>
  );
}

const meta = {
  title: "Shared/Auth/Password field",
  component: PasswordForm,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "A password input with a show button, on sign in, the invite and reset links and the account page. The button keeps its name, Show password, and says by its pressed state whether the text is visible.",
      },
    },
  },
} satisfies Meta<typeof PasswordForm>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Hidden at first; the eye shows it, a second press hides it again. */
export const Hidden: Story = {
  play: async ({ canvasElement }) => {
    const page = within(canvasElement);
    const input = page.getByLabelText("Password");
    const show = page.getByRole("button", { name: "Show password" });
    await expect(input).toHaveAttribute("type", "password");
    await userEvent.click(show);
    await expect(input).toHaveAttribute("type", "text");
    await expect(show).toHaveAttribute("aria-pressed", "true");
  },
};

/** With the rule a new password must follow. */
export const WithHint: Story = {
  name: "With a hint",
  args: { description: PASSWORD_HINT },
};

export const Disabled: Story = { args: { disabled: true } };
