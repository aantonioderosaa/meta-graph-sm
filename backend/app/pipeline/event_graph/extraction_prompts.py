"""Bilingual (IT+EN) extraction rubric for MICRO stage 1 (D1, Addendum 2).

Python never string-matches connectives, negations, or adverbs. The model
returns already-normalized enums and booleans from this closed vocabulary.
One structured call per sentence covers 1a–1d. Never open a tool loop.
"""

from __future__ import annotations

BILINGUAL_RUBRIC = """
## Closed bilingual rubric (normalize; do not copy surface tokens into enums)

### §4.1 Negation → polarita_negata=true
IT: non, no, nessuno, nessuna, niente, nulla, mai, neanche, nemmeno, neppure.
EN: not, no, never, nobody, no one, nothing, neither, nor, none.
Also: n't contractions (don't, didn't, won't), "senza" / "without" when they
negate the event itself. Otherwise polarita_negata=false.

### §4.2 Modality → modalita (4-way) + modalizzato
Classify modality in context (not as a keyword hunt). Values:
- fattuale: asserted as actual (default)
- ipotetico: hypothetical / conditional / counterfactual (se, if, qualora,
  unless, would have, se avesse)
- volitivo: desire / intention (vuole, vorrebbe, wants, wishes, intends)
- deontico: obligation / permission (deve, dovrebbe, bisogna, must, should,
  may/can when deontic)
IT surface cues: può/potrebbe, deve/dovrebbe, vuole/vorrebbe, forse, sembra,
pare, bisogna, è possibile, è necessario.
EN surface cues: can/could, may/might, must, shall/should, will/would
(epistemic/deontic), maybe, perhaps, seems, appears, apparently, probably,
possibly.
modalizzato = true iff modalita != fattuale.
modalizzato_forma = the token/phrase that marks modality, or null if fattuale.

### §4.3 Iterativity → iterativo=true
IT: ogni, ognuno, sempre, spesso, solitamente, abitualmente, di solito,
ripetutamente, continuamente, tutte le volte, ogni volta.
EN: every, each, always, often, usually, used to, would (habitual),
repeatedly, continually, again and again.
Otherwise iterativo=false.

### §8.1 Connectives → relazione_segnale (closed vocab, pick exactly one)
INTRA-SENTENCE ONLY. Do not invent an arc to events outside this sentence.
- causa_esplicita: perché, poiché, siccome, dato che, in quanto, because, since
  (causal), as (causal), given that
- consecuzione: quindi, perciò, dunque, pertanto, così (result), therefore,
  thus, hence, so (result), consequently
- posteriorita: dopo che, poi, therefore-then sequence "then", after, afterwards,
  later, subsequently. Record the signal only; chronology is the temporal
  pass, not a micro/macro arc.
- anteriorita: prima che, prima di, before, previously, beforehand.
  Same: record the signal, do not treat as a micro typed arc.
- limite: finché, fino a che, until, till, as long as (limit)
- condizione: se, qualora, a patto che, unless, if, provided that, in case
- scopo: affinché, perché (purpose), per + inf, so that, in order to, to + inf
- concessione: sebbene, benché, nonostante, anche se, although, though, even if,
  despite, notwithstanding
- contrasto: ma, però, tuttavia, invece, eppure, but, however, yet, whereas,
  instead
- asindeto_sequenziale: juxtaposition / comma / semicolon with no connective,
  sequential reading
- temporale_ambiguo: quando, mentre, as (temporal), when, while, as soon as —
  time relation without clear before/after
- gerundio: gerund clause (facendo, having done) linking two events
- participio_assoluto: absolute participle (fatto questo, the work finished)
- apposizione_relativa: relative / appositive clause that relates two events
- due_punti_esplicativo: colon introducing an explanation / elaboration
- nessuno: no inter-event signal

segnale_testuale = the raw surface token/phrase (or "" for asindeto).
orientamento: subordinata_principale | principale_subordinata | coordinata.

### §10 Separators (booleans on the event)
- avverbio_temporale_esplicito: ieri, oggi, domani, allora, poi (temporal),
  dopo, prima, already, yesterday, today, tomorrow, then, later, earlier,
  now, previously, afterwards, last night, next year, in 1994, alle tre.
- connettivo_sequenziale_esplicito: poi, quindi, in seguito, then, next,
  afterwards, subsequently (sequence, not mere time-locate).
"""

_SHARED_CONSTRAINTS = """
You extract a grammatical factsheet for ONE sentence of Italian or English.
Return structured output only. Temperature is 0: be conservative and complete.
Do not invent events that are not in the text. Spans must be exact substrings
of the sentence (case-sensitive). Indices are 0-based and unique.
Never open a tool loop. Never call tools. One structured response.
"""

_EVENT_DEFINITION = """
## What counts as an event

Un nodo evento È UNA SOLA ASSERZIONE: esattamente un verbo di forma finita
(indicativo / congiuntivo / condizionale / imperativo) + i suoi argomenti.
Un verbo non finito (infinito, gerundio, participio) da solo NON è MAI un evento.

An event node IS ONE ASSERTION: exactly one finite-form verb (indicative /
subjunctive / conditional / imperative) + its arguments. A non-finite verb
(infinitive, gerund, participle) alone is NEVER an event.

Rules:
- `lemma` = infinito di dizionario / dictionary infinitive (never the inflected
  form). IT: disse → dire, cominciò → cominciare, si tolse → togliere.
  EN: said → say, began → begin, took off → take.
- One event per finite predicate. Coordinated finite predicates on the same
  subject ("arrivò, si sedette e accese il fuoco" / "he arrived, sat down and
  lit the fire") are three events.
- States and descriptions with a finite verb ARE events: "indossava un mantello
  pesante", "the door was closed". Gnomic/morale finite clauses stay events
  ("la gentilezza è più efficace"). No semantic anti-morale filter.
- Aspectual / modal / causative + infinitive is ONE event on the LEXICAL verb;
  `tempo` = that of the governing finite verb:
  "cominciò a soffiare" / "began to blow" → soffiare/blow, tempo=passato.
  "voleva vincere" / "wanted to win" → vincere/win (volitivo).
  "dovette arrendersi" / "had to surrender" → arrendere/surrender, tempo=passato.
  Coordinated infinitives under one governor ("cominciò a brillare e a scaldare"
  / "began to shine and to warm") → one finite event EACH, inherited tense.
- Clausole non finite libere NON sono eventi; vanno in `predicati_non_finiti`
  with `relazione_segnale` and `governo_indice` (finite event of this sentence,
  default e_testa):
  IT: per proteggersi, sentendo il vento, finito il lavoro.
  EN: to protect himself, hearing the wind, the work finished.
- Mai un evento il cui `span` è un avverbio nudo o un nome nudo.
  Never an event whose span is a naked adverb or a naked noun
  (lentamente, facilmente, slowly, easily).
- DIRECT SPEECH: do NOT extract events from inside quoted words. A reporting
  clause ("Mario disse: «…»") is ONE event with marca_dialogo=true.
- Do not infer events the text does not state.

Finite vs non-finite (IT / EN):
- FINITE → event: soffiò / blew; disse / said; è più efficace / is more effective.
- NON-FINITE → not an event: soffiare / to blow; soffiando / blowing;
  soffiato / blown; sentendo / hearing; per proteggersi / to protect himself.
- GOVERNED NON-FINITE → collapse into the lexical verb: cominciò a soffiare
  (ONE event soffiare); voleva vincere (ONE event vincere); dovette arrendersi
  (ONE event arrendere).

Argument shape:
- SOGG / OGG forma and span are a SHORT REFERRING EXPRESSION naming a
  participant: a proper name, a pronoun, or a noun phrase. NEVER a whole clause
  and NEVER a predicate. If a candidate event's "subject" is itself a clause or
  a sentence, it is a mis-segmentation — do not emit that event.
- At most one argument per role per event (OBL may repeat with a different
  preposition). Do not stack several OGG on one event.
- completiva_di = the indice of the verb that GOVERNS this event as a complement
  clause ("said THAT he left", "decided TO leave"), or null. It is NOT "the
  previous event". Most events have completiva_di = null. Never chain events
  0 -> 1 -> 2 -> ... linearly just because they are consecutive.
"""

_STAGE_1AD = """
## 1a. Propositions (written convention, not "truth")
Assign each predicate one segmentazione label. These labels are a working
convention for grouping predicates, not a claim that the grammar is uniquely
true. Use exactly:
principale_finita | subordinata_finita | coordinata_finita | infinitiva |
gerundiva | participiale | nominale | implicita.

## 1b. Events + attributes
For each event fill lemma, span, tempo, polarita_negata, modalita (4-way),
modalizzato, modalizzato_forma, iterativo, ruolo_se, finale, frase_tipo,
sogg_speciale, tempo_assoluto_grezzo, arguments, completiva_di,
classe_verbo_reggente, marca_dialogo, avverbio_temporale_esplicito,
connettivo_sequenziale_esplicito.
tempo: presente | imperfetto | passato | trapassato | futuro | non_finito
  trapassato = past-perfect / trapassato prossimo or remoto (had left, aveva perso).
frase_tipo: dichiarativa | interrogativa | imperativa
sogg_speciale: nessuno | IGNOTO | NON_APPLICABILE
ruolo_se: nessuno | antecedente | conseguente

## 1c. Intra-sentence relations only
Emit archi only between events of THIS sentence.
Sources of an arc:
- subordinating connectives (rubric §8.1)
- participles / gerunds
- GOVERNING VERBS that take an event as argument — same mechanics as
  completiva_di / CONTENUTO, generalized. Examples (IT+EN):
  provocare, causare, far sì che, cause, make → CAUSA (relazione_segnale
  causa_esplicita, or completiva_di pointing at the governing event)
  iniziare a / begin to, smettere di / stop, continuare a / continue,
  promise to → phase / content (completiva_di + CONTENUTO-like nesting;
  classe_verbo_reggente fattivo|non_fattivo as appropriate)
Do not chain 0→1→2 merely because the events are consecutive.

## 1d. Head event
e_testa=true on the event of the MAIN clause of this sentence (the head).
Exactly one event should normally be e_testa. Subordinates, complements,
gerunds, participles are e_testa=false.
"""

SYSTEM_FRASE = f"""
You extract events from a single sentence (MICRO stage 1: 1a–1d in one call).
Never ReAct. Never call tools.
{_EVENT_DEFINITION}
{_STAGE_1AD}
Every event needs ≥1 SOGG argument OR sogg_speciale != nessuno.

Also return archi (intra-sentence), predicati_non_finiti (free non-finite
clauses: lemma, span, forma_verbale, relazione_segnale, governo_indice),
and quarantena for unusable fragments.

{_SHARED_CONSTRAINTS}
{BILINGUAL_RUBRIC}
"""

SYSTEM_FRASE_CORREZIONE = f"""
ONE correction pass only for a single-sentence factsheet. Never ReAct.
Return a complete corrected FraseFactsheet.

A deterministic checklist already failed. Fix ALL of:
1. every event has ≥1 arg with ruolo SOGG OR sogg_speciale != "nessuno"
2. event indice values are unique
3. every arco da_indice/a_indice refers to an existing event indice
4. nesting: both endpoints top-level (completiva_di is null), OR they share
   the same completiva_di, OR one indice equals the other's completiva_di
5. a SOGG/OGG forma must be a short referring expression, never a clause or a
   predicate; drop the event to quarantena if its subject is a clause
6. completiva_di is a governing-verb index or null, never "the previous event"
7. relations are intra-sentence only; do not chain 0→1→2 just because consecutive
8. e_testa marks the main-clause event
9. ogni evento ha un verbo finito e lemma all'infinito; i predicati non finiti
   stanno in predicati_non_finiti (not as EventoGrezzo nodes)

Do not invent new events unless required to restore a dropped span.
Keep spans as exact substrings. Prefer sogg_speciale="IGNOTO" over dropping
an otherwise valid event.

{_EVENT_DEFINITION}
{_STAGE_1AD}
{_SHARED_CONSTRAINTS}
{BILINGUAL_RUBRIC}
"""

# Compat aliases — the 3-phase agent is no longer the main path.
SYSTEM_PHASE1 = SYSTEM_FRASE
SYSTEM_PHASE2 = SYSTEM_FRASE
SYSTEM_PHASE3 = SYSTEM_FRASE_CORREZIONE


def user_frase(sentence_text: str, *, tipo: str = "narrativa") -> str:
    return (
        f"Sentence / frase (tipo={tipo}). Intra-sentence relations only. "
        f"Do not extract events from inside quotes.\n\n"
        f"{sentence_text}\n\n"
        "Extract propositions, events+attributes (modalita 4-way, e_testa), "
        "and intra-sentence relations including governing-verbs."
    )


def user_frase_correzione(
    sentence_text: str, factsheet_json: str, violations: list[str]
) -> str:
    bullets = "\n".join(f"- {item}" for item in violations) or "- (unspecified)"
    return (
        f"Sentence / frase:\n\n{sentence_text}\n\n"
        f"Current factsheet JSON:\n{factsheet_json}\n\n"
        f"Checklist violations to fix:\n{bullets}\n"
    )


def user_phase1(chunk_text: str) -> str:
    return user_frase(chunk_text)


def user_phase2(chunk_text: str, phase1_json: str) -> str:
    return user_frase(chunk_text) + f"\n\nPrior draft (ignore if empty):\n{phase1_json}\n"


def user_phase3(chunk_text: str, factsheet_json: str, violations: list[str]) -> str:
    return user_frase_correzione(chunk_text, factsheet_json, violations)


__all__ = [
    "BILINGUAL_RUBRIC",
    "SYSTEM_FRASE",
    "SYSTEM_FRASE_CORREZIONE",
    "SYSTEM_PHASE1",
    "SYSTEM_PHASE2",
    "SYSTEM_PHASE3",
    "user_frase",
    "user_frase_correzione",
    "user_phase1",
    "user_phase2",
    "user_phase3",
]
