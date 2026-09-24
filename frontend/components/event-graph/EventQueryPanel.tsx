"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { queryNl, queryStructured } from "@/lib/event-graph/api";
import {
  HIGHLIGHT_COLORS,
  idsFromNlQuery,
  idsFromQueryResult,
  mergeHighlights,
  type HighlightKind,
} from "@/lib/event-graph/highlight";
import {
  QUERY_FATTUALITA,
  QUERY_PIANI,
  QUERY_RELAZIONI,
  QUERY_TAB_ORDER,
  QUERY_TEMPI,
  QUERY_TRAVERSALS,
  buildEventQuerySpec,
  structuredQueryReady,
  type QueryTab,
} from "@/lib/event-graph/query-spec";
import type {
  EventNlQueryResponse,
  EventQueryEvento,
  EventQueryRisultato,
  EventQuerySpec,
  EventStructuredQueryResponse,
  Fattualita,
  PianoNarrativo,
  TempoVerbale,
  TipoRelazione,
  TraversalKind,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

const fieldClass =
  "rounded border border-input bg-background px-2 py-1.5 text-sm";

const TRAVERSAL_HINT: Record<TraversalKind, string> = {
  catena_di: "stessa catena dell'evento bersaglio",
  spina_dorsale_di: "cammino SEQUENZA (non ancore)",
  prima_di: "ancore precedenti (APPARTIENE_A + SUCCESSIONE_ANCORA)",
  dopo_di: "ancore successive (APPARTIENE_A + SUCCESSIONE_ANCORA)",
  vicinato_temporale: "ancore vicine sulla SUCCESSIONE_ANCORA",
};

type EventQueryPanelProps = {
  onHighlightsChange?: (highlights: Record<string, HighlightKind>) => void;
};

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span>{label}</span>
      {children}
      {hint ? (
        <span className="text-[10px] leading-snug text-muted-foreground">
          {hint}
        </span>
      ) : null}
    </label>
  );
}

function ResultList({
  risultato,
  kind,
  cited,
}: {
  risultato: EventQueryRisultato | null;
  kind: HighlightKind;
  cited?: string[];
}) {
  if (!risultato) {
    return <p className="text-xs text-muted-foreground">Nessun risultato.</p>;
  }
  const eventi = risultato.eventi ?? [];
  const citedSet = new Set(cited ?? []);
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-foreground" data-testid={`${kind}-count`}>
        <span
          className="mr-1 inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: HIGHLIGHT_COLORS[kind] }}
          aria-hidden
        />
        {eventi.length} eventi
        {risultato.archi?.length ? ` · ${risultato.archi.length} archi` : ""}
      </p>
      <ul className="max-h-36 overflow-y-auto text-xs">
        {eventi.map((evento: EventQueryEvento) => (
          <li key={evento.id} className="truncate" title={evento.lemma ?? evento.id}>
            {citedSet.has(evento.id) ? (
              <span className="mr-1 text-muted-foreground">citato</span>
            ) : null}
            {evento.lemma ?? "—"}{" "}
            <code className="rounded bg-muted px-1">{evento.id}</code>
            {evento.tempo_assoluto ? (
              <span className="ml-1 text-muted-foreground">
                {String(evento.tempo_assoluto)}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EventQueryPanel({ onHighlightsChange }: EventQueryPanelProps) {
  const [tab, setTab] = useState<QueryTab>(QUERY_TAB_ORDER[0]);
  const [spec, setSpec] = useState<EventQuerySpec>({});
  const [finestraDa, setFinestraDa] = useState("");
  const [finestraA, setFinestraA] = useState("");
  const [nlText, setNlText] = useState("");

  const [structuredResult, setStructuredResult] =
    useState<EventStructuredQueryResponse | null>(null);
  const [nlResult, setNlResult] = useState<EventNlQueryResponse | null>(null);
  const [structuredPinned, setStructuredPinned] = useState(false);
  const [nlPinned, setNlPinned] = useState(false);
  const [structuredBusy, setStructuredBusy] = useState(false);
  const [nlBusy, setNlBusy] = useState(false);
  const [structuredError, setStructuredError] = useState<string | null>(null);
  const [nlError, setNlError] = useState<string | null>(null);

  useEffect(() => {
    onHighlightsChange?.(
      mergeHighlights(
        idsFromQueryResult(structuredResult?.risultato),
        idsFromNlQuery(nlResult),
      ),
    );
  }, [structuredResult, nlResult, onHighlightsChange]);

  function patchSpec<K extends keyof EventQuerySpec>(
    key: K,
    value: EventQuerySpec[K] | "",
  ) {
    setSpec((prev) => {
      const next = { ...prev };
      if (value === "" || value == null) {
        delete next[key];
      } else {
        next[key] = value;
      }
      return next;
    });
  }

  const readySpec = buildEventQuerySpec(spec, finestraDa, finestraA);

  async function onStructuredSubmit(event: FormEvent) {
    event.preventDefault();
    if (!structuredQueryReady(readySpec)) return;
    setStructuredBusy(true);
    setStructuredError(null);
    try {
      const result = await queryStructured(readySpec);
      if (!structuredPinned) setStructuredResult(result);
    } catch (err) {
      setStructuredError(err instanceof Error ? err.message : "Query fallita");
    } finally {
      setStructuredBusy(false);
    }
  }

  async function onNlSubmit(event: FormEvent) {
    event.preventDefault();
    if (!nlText.trim()) return;
    setNlBusy(true);
    setNlError(null);
    try {
      const result = await queryNl(nlText);
      if (!nlPinned) setNlResult(result);
    } catch (err) {
      setNlError(err instanceof Error ? err.message : "Query fallita");
    } finally {
      setNlBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">Query eventi</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <Tabs
          value={tab}
          onValueChange={(value) => setTab(value as QueryTab)}
        >
          <TabsList className="grid h-auto w-full grid-cols-2">
            <TabsTrigger value="nl" className="text-xs">
              Linguaggio naturale
            </TabsTrigger>
            <TabsTrigger value="structured" className="text-xs">
              Strutturata
            </TabsTrigger>
          </TabsList>

          <TabsContent value="nl" className="mt-3">
            <form
              onSubmit={(e) => void onNlSubmit(e)}
              className="flex flex-col gap-2"
            >
              <Field
                label="domanda"
                hint="Compilata in EventQuerySpec (di solito solo testo fulltext), poi risposta sui fatti recuperati."
              >
                <textarea
                  className={cn(fieldClass, "min-h-[6rem]")}
                  name="testo"
                  value={nlText}
                  onChange={(e) => setNlText(e.target.value)}
                  placeholder="Chi è arrivato prima della pioggia?"
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="submit"
                  size="sm"
                  disabled={nlBusy || !nlText.trim()}
                >
                  {nlBusy ? "Invio…" : "Esegui"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={nlPinned ? "default" : "outline"}
                  onClick={() => setNlPinned((v) => !v)}
                >
                  {nlPinned ? "Sblocca risultato" : "Blocca risultato"}
                </Button>
              </div>
              {nlError ? (
                <p className="text-xs text-destructive" role="alert">
                  {nlError}
                </p>
              ) : null}
              {nlResult?.risposta ? (
                <p
                  className="rounded bg-muted p-2 text-xs leading-snug"
                  data-testid="nl-risposta"
                >
                  {nlResult.risposta}
                </p>
              ) : null}
              {nlResult?.spec_generata ? (
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    spec_generata
                  </summary>
                  <pre
                    className="mt-1 max-h-36 overflow-auto rounded bg-muted p-2 text-[10px] leading-snug"
                    data-testid="spec-generata"
                  >
                    {JSON.stringify(nlResult.spec_generata, null, 2)}
                  </pre>
                </details>
              ) : null}
              <ResultList
                risultato={nlResult?.risultato ?? null}
                kind="nl"
                cited={nlResult?.eventi_citati}
              />
            </form>
          </TabsContent>

          <TabsContent value="structured" className="mt-3">
            <form
              onSubmit={(e) => void onStructuredSubmit(e)}
              className="flex flex-col gap-2"
            >
              <Field
                label="testo"
                hint="Ricerca fulltext sul testo dell'evento. È il filtro principale del sistema attuale."
              >
                <input
                  className={fieldClass}
                  name="testo"
                  value={spec.testo ?? ""}
                  onChange={(e) => patchSpec("testo", e.target.value)}
                  placeholder="vento, pioggia, arrivo…"
                />
              </Field>
              <Field
                label="lemma"
                hint="Match esatto della frase intera memorizzata, non un nome o una parafrasi."
              >
                <input
                  className={fieldClass}
                  name="lemma"
                  value={spec.lemma ?? ""}
                  onChange={(e) => patchSpec("lemma", e.target.value)}
                />
              </Field>
              <Field label="documento">
                <input
                  className={fieldClass}
                  name="documento"
                  value={spec.documento ?? ""}
                  onChange={(e) => patchSpec("documento", e.target.value)}
                />
              </Field>
              <Field
                label="tipo_relazione"
                hint="APPARTIENE_A e SUCCESSIONE_ANCORA sono tipi attivi; CAUSA e simili solo se presenti nel grafo."
              >
                <select
                  className={fieldClass}
                  name="tipo_relazione"
                  value={spec.tipo_relazione ?? ""}
                  onChange={(e) =>
                    patchSpec(
                      "tipo_relazione",
                      e.target.value as TipoRelazione | "",
                    )
                  }
                >
                  <option value="">—</option>
                  {QUERY_RELAZIONI.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field
                  label="finestra da"
                  hint="ISO YYYY / YYYY-MM / YYYY-MM-DD"
                >
                  <input
                    className={fieldClass}
                    name="finestra_da"
                    value={finestraDa}
                    onChange={(e) => setFinestraDa(e.target.value)}
                    placeholder="1843"
                  />
                </Field>
                <Field label="finestra a">
                  <input
                    className={fieldClass}
                    name="finestra_a"
                    value={finestraA}
                    onChange={(e) => setFinestraA(e.target.value)}
                    placeholder="1843-12-25"
                  />
                </Field>
              </div>
              <Field
                label="traversal"
                hint={
                  spec.traversal
                    ? TRAVERSAL_HINT[spec.traversal]
                    : "prima_di / dopo_di / vicinato camminano le ancore, non SEQUENZA."
                }
              >
                <select
                  className={fieldClass}
                  name="traversal"
                  value={spec.traversal ?? ""}
                  onChange={(e) =>
                    patchSpec(
                      "traversal",
                      e.target.value as TraversalKind | "",
                    )
                  }
                >
                  <option value="">—</option>
                  {QUERY_TRAVERSALS.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="traversal_target"
                hint="Id dell'evento bersaglio. Obbligatorio se c'è un traversal."
              >
                <input
                  className={fieldClass}
                  name="traversal_target"
                  value={spec.traversal_target ?? ""}
                  onChange={(e) =>
                    patchSpec("traversal_target", e.target.value)
                  }
                />
              </Field>
              <Field
                label="piano"
                hint="Filtro sul nodo. Nella pipeline attuale spesso è vuoto."
              >
                <select
                  className={fieldClass}
                  name="piano"
                  value={spec.piano ?? ""}
                  onChange={(e) =>
                    patchSpec("piano", e.target.value as PianoNarrativo | "")
                  }
                >
                  <option value="">—</option>
                  {QUERY_PIANI.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="fattualità"
                hint="Oggi gli eventi estratti sono in pratica tutti FATTUALE."
              >
                <select
                  className={fieldClass}
                  name="fattualita"
                  value={spec.fattualita ?? ""}
                  onChange={(e) =>
                    patchSpec("fattualita", e.target.value as Fattualita | "")
                  }
                >
                  <option value="">—</option>
                  {QUERY_FATTUALITA.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="tempo"
                hint="Tempo verbale del nodo, incluso trapassato. Spesso assente."
              >
                <select
                  className={fieldClass}
                  name="tempo"
                  value={spec.tempo ?? ""}
                  onChange={(e) =>
                    patchSpec("tempo", e.target.value as TempoVerbale | "")
                  }
                >
                  <option value="">—</option>
                  {QUERY_TEMPI.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="fonte">
                <input
                  className={fieldClass}
                  name="fonte"
                  value={spec.fonte ?? ""}
                  onChange={(e) => patchSpec("fonte", e.target.value)}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="submit"
                  size="sm"
                  disabled={
                    structuredBusy || !structuredQueryReady(readySpec)
                  }
                >
                  {structuredBusy ? "Invio…" : "Esegui"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={structuredPinned ? "default" : "outline"}
                  onClick={() => setStructuredPinned((v) => !v)}
                >
                  {structuredPinned ? "Sblocca risultato" : "Blocca risultato"}
                </Button>
              </div>
              {structuredError ? (
                <p className="text-xs text-destructive" role="alert">
                  {structuredError}
                </p>
              ) : null}
              <ResultList
                risultato={structuredResult?.risultato ?? null}
                kind="structured"
              />
            </form>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
