import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";

import { isApiError } from "@/lib/api";

import {
  addBankAnswer,
  answerBankKey,
  getAnswerBank,
  saveBankAnswer,
  type Bank,
} from "./settings-api";

type Handlers<Variables> = Pick<
  UseMutationOptions<Bank, Error, Variables>,
  "onMutate" | "onSuccess" | "onError"
>;

/** The person's saved answers, reused when the same question comes back on another form. */
export function useAnswerBank() {
  return useQuery({
    queryKey: answerBankKey,
    queryFn: ({ signal }) => getAnswerBank(signal),
  });
}

/** Adds a saved answer; a question saved already elsewhere (409) reads the list again. */
export function useAddBankAnswer(
  handlers: Handlers<{ question: string; answer: string }>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ question, answer }: { question: string; answer: string }) =>
      addBankAnswer(question, answer),
    ...handlers,
    onSuccess: (...args) => {
      client.setQueryData<Bank>(answerBankKey, args[0]);
      return handlers.onSuccess?.(...args);
    },
    onError: (...args) => {
      const [error] = args;
      if (isApiError(error) && error.status === 409)
        void client.invalidateQueries({ queryKey: answerBankKey });
      return handlers.onError?.(...args);
    },
  });
}

type BankChange = { key: string; answer: string | null };

/** Changes a saved answer, or forgets it (null); one forgotten elsewhere (404) reads the list again. */
export function useSaveBankAnswer<Change extends BankChange>(
  handlers: Handlers<Change>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ key, answer }: Change) => saveBankAnswer(key, answer),
    ...handlers,
    onSuccess: (...args) => {
      client.setQueryData<Bank>(answerBankKey, args[0]);
      return handlers.onSuccess?.(...args);
    },
    onError: (...args) => {
      const [error] = args;
      if (isApiError(error) && error.status === 404)
        void client.invalidateQueries({ queryKey: answerBankKey });
      return handlers.onError?.(...args);
    },
  });
}
