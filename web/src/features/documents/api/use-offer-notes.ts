import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  confirmDocument,
  documentsKey,
  failureSentence as uploadFailure,
  listDocuments,
  uploadDocument,
} from "@/features/ingestion/api/documents-api";
import {
  acceptConsent,
  consentKey,
  getConsent,
} from "@/features/ingestion/api/runs-api";

import { failureSentence } from "./documents-api";
import { getOfferNotes, offerNotesKey, readOffer } from "./offer-notes-api";
import { POLL_MS } from "../helpers/labels";
import { NOT_A_LETTER } from "../helpers/offer-notes";
import { isCvFile, isRead, READING_POLL_MS } from "../helpers/upload-cv";

/** The posting's notes, read again while a reading or a chat change is open. */
export function useOfferNotes(jobId: string) {
  return useQuery({
    queryKey: offerNotesKey(jobId),
    queryFn: ({ signal }) => getOfferNotes(jobId, signal),
    refetchInterval: (current) =>
      current.state.data?.busy === true ? POLL_MS : false,
  });
}

/** Once a chat answer has `ended`, the notes are read again with it. */
export function useRefreshOfferNotes(jobId: string, ended: boolean) {
  const client = useQueryClient();
  useEffect(() => {
    if (ended)
      void client.invalidateQueries({ queryKey: offerNotesKey(jobId) });
  }, [ended, client, jobId]);
}

/**
 * A letter on its way to notes: the file is sent to ingestion, which reads its text; once read,
 * kept when it may hold a secret, and once ingestion's consent is given, its reading starts,
 * once. `again` reads a letter already uploaded through the same steps.
 */
export function useReadOffer(jobId: string) {
  const client = useQueryClient();
  const [problem, setProblem] = useState<string>();
  const [letterId, setLetterId] = useState<string>();
  /** The letter whose reading was asked for: once per choice. */
  const started = useRef<string>(undefined);

  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
    enabled: letterId !== undefined,
  });
  const files = useQuery({
    queryKey: documentsKey,
    queryFn: ({ signal }) => listDocuments(signal),
    enabled: letterId !== undefined,
    refetchInterval: (current) =>
      current.state.data?.items.find((one) => one.id === letterId)?.status ===
      "pending"
        ? READING_POLL_MS
        : false,
  });

  const send = useMutation({
    mutationFn: (file: File) => uploadDocument(file),
    onSuccess: (stored) => {
      setLetterId(stored.id);
      void client.invalidateQueries({ queryKey: documentsKey });
    },
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });
  const start = useMutation({
    mutationFn: (id: string) => readOffer(jobId, id),
    onSuccess: (notes) => {
      client.setQueryData(offerNotesKey(jobId), notes);
      setLetterId(undefined);
    },
    onError: (error) => {
      setLetterId(undefined);
      setProblem(failureSentence(error));
    },
  });
  const agree = useMutation({
    mutationFn: acceptConsent,
    onSuccess: (accepted) => {
      client.setQueryData(consentKey, accepted);
    },
    onError: (error) => {
      setProblem(failureSentence(error));
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });
  const keep = useMutation({
    mutationFn: confirmDocument,
    onSuccess: () => client.invalidateQueries({ queryKey: documentsKey }),
    onError: (error) => {
      setProblem(uploadFailure(error));
    },
  });

  const file = files.data?.items.find((one) => one.id === letterId);
  const read = isRead(file);
  const accepted = consent.data?.accepted;
  const startNow = start.mutate;
  useEffect(() => {
    if (!read || letterId === undefined || accepted !== true) return;
    if (started.current === letterId) return;
    started.current = letterId;
    startNow(letterId);
  }, [read, letterId, accepted, startNow]);

  const choose = (id: string | undefined) => {
    setProblem(undefined);
    started.current = undefined;
    setLetterId(id);
  };

  return {
    problem,
    file,
    consent,
    agree,
    keep,
    /** A file is on its way: sent, being read, waiting for the consent or a keep. */
    pending: letterId !== undefined || send.isPending || start.isPending,
    /** Ingestion's consent is due: the letter is read and nothing was agreed to yet. */
    asking: read && accepted === false,
    /** A file chosen: the steps start over with it. */
    take: (chosen: File | undefined) => {
      if (chosen === undefined) return;
      choose(undefined);
      if (!isCvFile(chosen.name)) {
        setProblem(NOT_A_LETTER);
        return;
      }
      send.mutate(chosen);
    },
    again: choose,
  };
}
