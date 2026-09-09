"use client";

import { useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  EventGraphApiError,
  fetchArcoDettaglio,
  fetchNodoDettaglio,
} from "@/lib/event-graph/api";
import type {
  ArcoDettaglio,
  CatenaNodo,
  CatenaOccorrenza,
  ElementSelection,
  NodoDettaglio,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

type ElementInspectorProps = {
  selection: ElementSelection | null;
  onSelectRelated?: (selection: ElementSelection) => void;
  className?: string;
};

type LoadState =
  | { status: "empty" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "nodo"; data: NodoDettaglio }
  | { status: "arco"; data: ArcoDettaglio };

/** Renders a value of unknown shape without losing information — the
 * dashboard must show EVERY property a node/edge carries, whatever its
 * type, since new fields appear across the pipeline without this
 * component ever being told about them in advance. */
function PropertyValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted-foreground">—</span>;
  }
  if (typeof value === "boolean") {
    return <span>{value ? "sì" : "no"}</span>;
  }
  if (typeof value === "number") {
    return <span className="font-mono">{value}</span>;
  }
  if (typeof value === "string") {
    return <span className="whitespace-pre-wrap break-words">{value}</span>;
  }
  // arrays / nested objects (es. tempo_assoluto {da,a}, tempo_assoluto_revisioni[])
  return (
    <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/60 p-1.5 font-mono text-[10px] leading-tight">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function PropertyTable({ proprieta }: { proprieta: Record<string, unknown> }) {
  const keys = Object.keys(proprieta)
    .filter((key) => key !== "id")
    .sort((a, b) => a.localeCompare(b));
  if (keys.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">Nessun&apos;altra proprietà.</p>
    );
  }
  return (
    <dl className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-2 gap-y-1.5 text-xs">
      {keys.map((key) => (
        <div key={key} className="contents">
          <dt className="truncate font-mono text-muted-foreground" title={key}>
            {key}
          </dt>
          <dd className="min-w-0">
            <PropertyValue value={proprieta[key]} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function EndpointChip({
  role,
  endpoint,
  onSelect,
}: {
  role: "da" | "a";
  endpoint: ArcoDettaglio["source"];
  onSelect?: (selection: ElementSelection) => void;
}) {
  const label = endpoint.label ?? endpoint.id ?? "?";
  const clickable = Boolean(endpoint.id && onSelect);
  return (
    <button
      type="button"
      disabled={!clickable}
      onClick={() => endpoint.id && onSelect?.({ kind: "nodo", id: endpoint.id })}
      className={cn(
        "rounded border border-border bg-muted/40 px-1.5 py-0.5 text-left text-xs",
        clickable && "cursor-pointer hover:bg-muted",
      )}
      title={endpoint.id ?? undefined}
    >
      <span className="text-muted-foreground">{role} </span>
      <span className="font-medium">{label}</span>
      {endpoint.labels.length > 0 ? (
        <span className="text-muted-foreground"> · {endpoint.labels.join(", ")}</span>
      ) : null}
    </button>
  );
}

function occorrenzaLabel(occ: CatenaOccorrenza): string {
  const bits = [occ.ancora, occ.sogg_forma, occ.tempo, occ.fattualita, occ.polarita]
    .filter((bit) => bit != null && String(bit) !== "")
    .map(String);
  return bits.length > 0 ? bits.join(" · ") : occ.id;
}

function CatenaSection({
  isEvento,
  catena,
  currentId,
  onSelect,
}: {
  isEvento: boolean;
  catena?: CatenaNodo;
  currentId: string;
  onSelect?: (selection: ElementSelection) => void;
}) {
  if (!isEvento) return null;
  const occorrenze = catena?.occorrenze ?? [];
  const hasChain = Boolean(catena) && occorrenze.length > 1;

  return (
    <section className="space-y-1.5 border-t border-border pt-2">
      <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Catena
      </h3>
      {!hasChain ? (
        <p className="text-xs text-muted-foreground">
          {occorrenze.length === 1
            ? "Occorrenza unica — nessuna altra voce in catena."
            : "Nessuna catena."}
        </p>
      ) : (
        <ol className="space-y-1">
          {occorrenze.map((occ, index) => {
            const isCurrent = occ.id === currentId;
            const clickable = Boolean(occ.id && onSelect && !isCurrent);
            return (
              <li key={occ.id ?? index} className="space-y-0.5">
                {index > 0 ? (
                  <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    {occ.ruolo ?? "—"}
                    {occ.divergenze?.length
                      ? ` · ${occ.divergenze.join(", ")}`
                      : ""}
                  </p>
                ) : null}
                <button
                  type="button"
                  disabled={!clickable}
                  onClick={() => occ.id && onSelect?.({ kind: "nodo", id: occ.id })}
                  className={cn(
                    "w-full rounded border border-border bg-muted/40 px-1.5 py-1 text-left text-xs",
                    clickable && "cursor-pointer hover:bg-muted",
                    isCurrent && "ring-1 ring-ring",
                  )}
                  title={occ.id}
                >
                  <span className="font-medium">{occorrenzaLabel(occ)}</span>
                  {occ.documento ? (
                    <span className="ml-1 text-muted-foreground">{occ.documento}</span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

export function ElementInspector({
  selection,
  onSelectRelated,
  className,
}: ElementInspectorProps) {
  const [state, setState] = useState<LoadState>({ status: "empty" });

  useEffect(() => {
    if (!selection) {
      setState({ status: "empty" });
      return;
    }
    let cancelled = false;
    setState({ status: "loading" });
    const load =
      selection.kind === "nodo"
        ? fetchNodoDettaglio(selection.id).then(
            (data): LoadState => ({ status: "nodo", data }),
          )
        : fetchArcoDettaglio(selection.id).then(
            (data): LoadState => ({ status: "arco", data }),
          );
    load
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof EventGraphApiError && err.status === 404
            ? "Elemento non più presente a grafo."
            : err instanceof Error
              ? err.message
              : "Impossibile caricare i dettagli.";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [selection]);

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Dettagli selezione</CardTitle>
      </CardHeader>
      <CardContent className="max-h-[28rem] overflow-y-auto pt-0">
        {state.status === "empty" ? (
          <p className="text-xs text-muted-foreground">
            Seleziona un nodo o una relazione nel grafo per vederne tutte le
            informazioni.
          </p>
        ) : null}
        {state.status === "loading" ? (
          <p className="text-xs text-muted-foreground">Carico…</p>
        ) : null}
        {state.status === "error" ? (
          <p className="text-xs text-destructive" role="alert">
            {state.message}
          </p>
        ) : null}
        {state.status === "nodo" ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-1">
              {state.data.labels.map((label) => (
                <span
                  key={label}
                  className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
                >
                  {label}
                </span>
              ))}
            </div>
            <p className="break-all font-mono text-[10px] text-muted-foreground">
              {state.data.id}
            </p>
            <PropertyTable proprieta={state.data.proprieta} />
            <CatenaSection
              isEvento={state.data.labels.includes("Evento")}
              catena={state.data.catena}
              currentId={state.data.id}
              onSelect={onSelectRelated}
            />
          </div>
        ) : null}
        {state.status === "arco" ? (
          <div className="space-y-2">
            <span className="inline-block rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
              {state.data.tipo}
            </span>
            <div className="flex flex-col gap-1">
              <EndpointChip
                role="da"
                endpoint={state.data.source}
                onSelect={onSelectRelated}
              />
              <EndpointChip
                role="a"
                endpoint={state.data.target}
                onSelect={onSelectRelated}
              />
            </div>
            <p className="break-all font-mono text-[10px] text-muted-foreground">
              {state.data.id}
            </p>
            <PropertyTable proprieta={state.data.proprieta} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
