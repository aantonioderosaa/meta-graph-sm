"use client";

/**
 * Documents page: ingest form + ingested document list (F3.3 / F3.5).
 * R3.3: reset KB with explicit two-step confirmation dialog.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ApiError,
  getDocuments,
  postDocuments,
  postDreamingRun,
  resetKnowledgeBase,
} from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { DocumentSummary } from "@/lib/types";

// Solo ciò che l'ingestione sa leggere (chunking.py lavora su testo semplice;
// niente parsing binario/PDF/docx). L'estensione è l'unico segnale lato
// client: `accept` filtra il selettore, il controllo qui sotto sul nome file
// è la vera barriera — un utente può forzare "tutti i file" nel dialog del
// sistema operativo, `accept` da solo non basta.
const ALLOWED_EXTENSIONS = [".md", ".txt"] as const;

function hasAllowedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function docIdFromFilename(filename: string): string {
  const base = filename.replace(/\.[^./\\]+$/, "");
  const slug = base
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "doc-1";
}

export function DocumentsPage() {
  const setActiveJobId = useAppStore((s) => s.setActiveJobId);
  const lastPipelineEvent = useAppStore((s) => s.lastPipelineEvent);
  const notifyKnowledgeBaseReset = useAppStore(
    (s) => s.notifyKnowledgeBaseReset,
  );

  const [docId, setDocId] = useState("doc-1");
  const [docText, setDocText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  // Tracks the job_id of an ingest we started, so we can auto-start dreaming
  // exactly when *that* ingest completes — never on a dreaming job's own
  // completion event (same stage/event names), which would loop forever.
  const autoDreamAfterJobIdRef = useRef<string | null>(null);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDocuments();
      setDocuments(data.documents);
    } catch (err) {
      setDocuments([]);
      if (err instanceof ApiError) {
        setError(`Impossibile caricare i documenti (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments, reloadToken]);

  const startDream = useCallback(
    async ({ auto }: { auto: boolean }) => {
      setBusy(true);
      if (!auto) setStatus(null);
      try {
        const { job_id } = await postDreamingRun({});
        setActiveJobId(job_id);
        setStatus(
          auto
            ? `Ingest completato — dream avviato automaticamente · job_id=${job_id}`
            : `Dreaming avviato · job_id=${job_id}`,
        );
      } catch (err) {
        setStatus(err instanceof Error ? err.message : "Dreaming fallito");
      } finally {
        setBusy(false);
      }
    },
    [setActiveJobId],
  );

  useEffect(() => {
    if (!lastPipelineEvent) return;
    if (
      lastPipelineEvent.stage === "done" &&
      lastPipelineEvent.event === "pipeline_complete"
    ) {
      setReloadToken((t) => t + 1);
    }

    const awaitedJobId = autoDreamAfterJobIdRef.current;
    if (!awaitedJobId || lastPipelineEvent.job_id !== awaitedJobId) return;

    if (
      lastPipelineEvent.stage === "done" &&
      lastPipelineEvent.event === "pipeline_complete"
    ) {
      autoDreamAfterJobIdRef.current = null;
      void startDream({ auto: true });
    } else if (lastPipelineEvent.stage === "failed") {
      autoDreamAfterJobIdRef.current = null;
      setStatus("Ingest fallito — dream non avviato automaticamente.");
    }
  }, [lastPipelineEvent, startDream]);

  async function onIngest() {
    if (!docText.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const { job_id } = await postDocuments({
        doc_id: docId || "doc-1",
        text: docText,
      });
      autoDreamAfterJobIdRef.current = job_id;
      setActiveJobId(job_id);
      setStatus(`Ingest avviato · job_id=${job_id} — il dream partirà da solo al completamento.`);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Ingest fallito");
    } finally {
      setBusy(false);
    }
  }

  function onPickFile() {
    setFileError(null);
    fileInputRef.current?.click();
  }

  async function onFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset immediately so selecting the same file twice still fires onChange.
    e.target.value = "";
    if (!file) return;
    if (!hasAllowedExtension(file.name)) {
      setFileError(
        `Formato non supportato (${file.name}). Solo .md e .txt — la pipeline legge testo semplice, non PDF/DOCX/altri binari.`,
      );
      return;
    }
    setFileError(null);
    try {
      const text = await file.text();
      setDocText(text);
      setDocId(docIdFromFilename(file.name));
      setStatus(`File caricato: ${file.name} (${text.length} caratteri) — controlla e premi Ingest.`);
    } catch {
      setFileError(`Impossibile leggere ${file.name}.`);
    }
  }

  async function onDream() {
    await startDream({ auto: false });
  }

  function openResetDialog() {
    setResetError(null);
    setResetOpen(true);
  }

  async function onConfirmReset() {
    setResetting(true);
    setResetError(null);
    try {
      await resetKnowledgeBase();
      // R3.4: clear shared client state + notify mounted views (graph, query history).
      notifyKnowledgeBaseReset();
      setResetOpen(false);
      setStatus("Knowledge base eliminata.");
      setReloadToken((t) => t + 1);
    } catch (err) {
      if (err instanceof ApiError) {
        setResetError(`Reset fallito (${err.status}): ${err.message}`);
      } else {
        setResetError(err instanceof Error ? err.message : "Reset fallito");
      }
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-4 md:p-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Documenti</h1>
          <p className="text-sm text-muted-foreground">
            Ingerisci testi e consulta i documenti già presenti nel grafo.
          </p>
        </div>
        <Button
          variant="destructive"
          size="sm"
          disabled={busy || resetting}
          onClick={openResetDialog}
        >
          Elimina tutto
        </Button>
      </header>

      <Dialog
        open={resetOpen}
        onOpenChange={(open) => {
          if (resetting) return;
          setResetOpen(open);
          if (!open) setResetError(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Eliminare l&apos;intera knowledge base?</DialogTitle>
            <DialogDescription>
              Questa azione cancella l&apos;intero grafo e tutti i documenti
              ingeriti. Non è reversibile.
            </DialogDescription>
          </DialogHeader>
          {resetError ? (
            <p className="text-sm text-destructive" role="alert">
              {resetError}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={resetting}
              onClick={() => setResetOpen(false)}
            >
              Annulla
            </Button>
            <Button
              variant="destructive"
              disabled={resetting}
              onClick={() => void onConfirmReset()}
            >
              {resetting ? "Eliminazione…" : "Conferma eliminazione"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <section
        aria-label="Form ingestione"
        className="space-y-3 border-b border-border/60 pb-6"
      >
        <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
          Ingestione
        </h2>
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <label className="flex min-w-[8rem] flex-col gap-1 text-xs">
            <span>doc_id</span>
            <input
              className="rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={docId}
              onChange={(e) => setDocId(e.target.value)}
            />
          </label>
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs">
            <span>testo documento</span>
            <textarea
              className="min-h-[4rem] rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={docText}
              onChange={(e) => setDocText(e.target.value)}
              placeholder="Incolla un paragrafo da ingerire…"
            />
          </label>
          <div className="flex gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".md,.txt,text/markdown,text/plain"
              className="hidden"
              onChange={(e) => void onFileSelected(e)}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={onPickFile}
            >
              Carica file (.md/.txt)
            </Button>
            <Button
              size="sm"
              disabled={busy || !docText.trim()}
              onClick={() => void onIngest()}
            >
              Ingest
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void onDream()}
              title="Ingest avvia il dream da solo al termine — usa questo solo per rilanciarlo a parte."
            >
              Dream (manuale)
            </Button>
          </div>
        </div>
        {fileError ? (
          <p className="text-xs text-destructive" role="alert">
            {fileError}
          </p>
        ) : null}
        {status ? <p className="text-xs text-foreground">{status}</p> : null}
      </section>

      <section aria-label="Elenco documenti" className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
            Documenti ingeriti
          </h2>
          <Button
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => setReloadToken((t) => t + 1)}
          >
            Aggiorna
          </Button>
        </div>

        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {loading && documents.length === 0 ? (
          <p className="text-xs text-muted-foreground">Caricamento…</p>
        ) : null}
        {!loading && !error && documents.length === 0 ? (
          <p className="text-xs text-muted-foreground">Nessun documento ingerito.</p>
        ) : null}

        {documents.length > 0 ? (
          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">doc_id</th>
                  <th className="px-3 py-2 font-medium">Chunk</th>
                  <th className="px-3 py-2 font-medium">Nodi</th>
                  <th className="px-3 py-2 font-medium">Ultimo aggiornamento</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.doc_id} className="border-b border-border/60 last:border-0">
                    <td className="px-3 py-2 font-mono text-xs">{doc.doc_id}</td>
                    <td className="px-3 py-2">{doc.chunk_count}</td>
                    <td className="px-3 py-2">{doc.node_count}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {doc.last_ingested_at || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
