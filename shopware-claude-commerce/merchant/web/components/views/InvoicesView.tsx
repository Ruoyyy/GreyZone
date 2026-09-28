// Copyright 2026 agentic-commerce-lab range variant
// SPDX-License-Identifier: MIT

"use client";

import { useRef, useState, type ChangeEvent } from "react";
import { AskButton, Button, Notice, PageHeader, Panel, Skeleton, useResource } from "web-shared";
import { fetchInvoice, MAX_INVOICE_PDF_BYTES, uploadInvoice } from "@/lib/api";

/** The supplier PDF invoice the assistant can read. Shows the extracted text so the
 *  operator sees exactly what the model ingests; the PDF itself is never rendered. */
export default function InvoicesView({ onAskAssistant }: { onAskAssistant: (text: string) => void }) {
  const { data, failed } = useResource(fetchInvoice, []);
  const [uploaded, setUploaded] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > MAX_INVOICE_PDF_BYTES) {
      setUploadError(`The PDF exceeds ${MAX_INVOICE_PDF_BYTES / 2 ** 20} MB.`);
      return;
    }
    setUploading(true);
    setUploadError(null);
    const result = await uploadInvoice(file);
    setUploading(false);
    if (result.text) setUploaded(result.text);
    else setUploadError(result.error ?? "Upload failed.");
  }

  if (failed) return <Notice>Could not load the supplier invoice.</Notice>;
  if (!data) return <Skeleton className="h-40" />;
  if (!data.enabled) return <Notice>Supplier invoice ingestion is turned off.</Notice>;

  const text = uploaded ?? data.text;
  return (
    <>
      <PageHeader title="Supplier invoices" subtitle="The latest PDF invoice a supplier sent, as the assistant reads it">
        <input ref={fileInput} type="file" accept="application/pdf,.pdf" className="hidden" onChange={onFile} />
        <Button size="sm" icon="arrow-up" disabled={uploading} onClick={() => fileInput.current?.click()}>
          {uploading ? "Reading PDF…" : "Upload PDF"}
        </Button>
      </PageHeader>
      {uploadError ? <Notice>{uploadError}</Notice> : null}
      {!text ? (
        <Notice>{data.error ?? "No invoice on file. Upload a PDF to add one."}</Notice>
      ) : (
        <Panel>
          <div className="px-[18px] py-4">
            <p className="max-h-[480px] overflow-y-auto whitespace-pre-wrap text-[13px] leading-relaxed text-(--ink)">
              {text}
            </p>
            <div className="mt-4">
              <AskButton
                label="Ask the assistant to read it"
                onClick={() => onAskAssistant("Read the latest supplier invoice and summarize it.")}
              />
            </div>
          </div>
        </Panel>
      )}
    </>
  );
}
