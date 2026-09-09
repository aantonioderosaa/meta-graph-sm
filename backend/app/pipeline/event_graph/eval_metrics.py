"""M-eval — gold corpus loader and separate segmentation / attribute / relation metrics.

Stdlib only. Isolated from legacy packages (D6). Gold contracts live here so
existing factsheet models stay untouched.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ALLEN_RELATIONS: frozenset[str] = frozenset(
    {
        "before",
        "after",
        "meets",
        "met_by",
        "overlaps",
        "overlapped_by",
        "during",
        "contains",
        "starts",
        "started_by",
        "finishes",
        "finished_by",
        "equals",
    }
)

TEMPI: frozenset[str] = frozenset(
    {"presente", "imperfetto", "passato", "futuro", "non_finito", "trapassato"}
)
MODALITA: frozenset[str] = frozenset(
    {"fattuale", "ipotetico", "volitivo", "deontico"}
)
FATTUALITA: frozenset[str] = frozenset({"FATTUALE", "NON_FATTUALE", "IPOTETICO"})
LANGS: frozenset[str] = frozenset({"it", "en"})
SENTENCE_TIPI: frozenset[str] = frozenset({"narrativa", "dialogo"})
SOGG_SPECIALI: frozenset[str] = frozenset({"nessuno", "IGNOTO", "NON_APPLICABILE"})
RUOLI: frozenset[str] = frozenset({"SOGG", "OGG", "OBL", "TEMPO", "LUOGO", "MODO"})
TIPI_SUPERFICIALI: frozenset[str] = frozenset(
    {"nome_proprio", "pronome", "sn_comune", "sogg_nullo"}
)

REQUIRED_PHENOMENA: frozenset[str] = frozenset(
    {
        "coordinated_predicates",
        "direct_speech",
        "state",
        "modal",
        "cause",
        "contrast",
        "pluperfect",
        "completive",
        "condition",
        "mentre",
        "asyndeton",
        "nested",
        "imperfect",
        "abbreviation",
        "decimal",
        "interrogative",
        "imperative",
        "future",
    }
)

ATTRIBUTE_FIELDS: tuple[str, ...] = (
    "lemma",
    "tempo",
    "modalita",
    "e_testa",
    "fattualita",
    "polarita_negata",
    "iterativo",
    "marca_dialogo",
    "sogg_speciale",
)

_ALIGN_THRESHOLD = 0.5


def default_corpus_path() -> Path:
    """``backend/tests/fixtures/event_graph_eval_corpus.json`` relative to this file."""
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "event_graph_eval_corpus.json"
    )


def load_corpus(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else default_corpus_path()
    raw = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        items = raw.get("items")
        if isinstance(items, list):
            return items
    raise ValueError(f"corpus at {target} is neither a list nor an object with 'items'")


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    if tp == 0 and fp == 0 and fn == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _inner_gold(blob: Any) -> dict[str, Any]:
    if not isinstance(blob, dict):
        return {}
    if "sentences" in blob or "eventi" in blob or "relazioni" in blob:
        return blob
    nested = blob.get("gold")
    return nested if isinstance(nested, dict) else {}


def _sentences_match(gold: Mapping[str, Any], pred: Mapping[str, Any]) -> bool:
    g_off = gold.get("offset_inizio"), gold.get("offset_fine")
    p_off = pred.get("offset_inizio"), pred.get("offset_fine")
    if None not in g_off and None not in p_off and g_off == p_off:
        return True
    g_txt = gold.get("testo"), gold.get("indice")
    p_txt = pred.get("testo"), pred.get("indice")
    if None not in (gold.get("testo"), gold.get("indice"), pred.get("testo"), pred.get("indice")):
        return g_txt == p_txt
    return False


def score_segmentation(
    gold_sentences: Sequence[Mapping[str, Any]] | None,
    pred_sentences: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    gold = _as_list(gold_sentences)
    pred = _as_list(pred_sentences)
    used_pred: set[int] = set()
    tp = 0
    for g in gold:
        found = None
        for i, p in enumerate(pred):
            if i in used_pred:
                continue
            if _sentences_match(g, p):
                found = i
                break
        if found is not None:
            used_pred.add(found)
            tp += 1
    fp = len(pred) - tp
    fn = len(gold) - tp
    return _prf(tp, fp, fn)


def _char_offsets(event: Mapping[str, Any]) -> tuple[int, int] | None:
    start = event.get("offset_inizio")
    end = event.get("offset_fine")
    if isinstance(start, int) and isinstance(end, int) and end > start:
        return start, end
    return None


def _span_iou(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    ao = _char_offsets(a)
    bo = _char_offsets(b)
    if ao is not None and bo is not None:
        inter = max(0, min(ao[1], bo[1]) - max(ao[0], bo[0]))
        union = max(ao[1], bo[1]) - min(ao[0], bo[0])
        return inter / union if union else 0.0
    sa = str(a.get("span") or "")
    sb = str(b.get("span") or "")
    if not sa or not sb:
        return 0.0
    if sa == sb:
        return 1.0
    if sa in sb or sb in sa:
        return min(len(sa), len(sb)) / max(len(sa), len(sb))
    # character-set Jaccard as a weak fallback for near-equal spans
    set_a, set_b = set(sa), set(sb)
    union = len(set_a | set_b)
    return len(set_a & set_b) / union if union else 0.0


def _event_match_score(gold: Mapping[str, Any], pred: Mapping[str, Any]) -> float:
    g_lemma = str(gold.get("lemma") or "").strip().lower()
    p_lemma = str(pred.get("lemma") or "").strip().lower()
    g_span = str(gold.get("span") or "").strip()
    p_span = str(pred.get("span") or "").strip()
    iou = _span_iou(gold, pred)
    score = iou
    if g_lemma and g_lemma == p_lemma:
        score += 1.0
    if g_span and g_span == p_span:
        score += 1.0
    return score


def align_events(
    gold_eventi: Sequence[Mapping[str, Any]] | None,
    pred_eventi: Sequence[Mapping[str, Any]] | None,
    *,
    threshold: float = _ALIGN_THRESHOLD,
) -> dict[int, int]:
    """Map gold ``indice`` → pred ``indice`` with greedy 1-1 matching."""
    gold = _as_list(gold_eventi)
    pred = _as_list(pred_eventi)
    candidates: list[tuple[float, int, int]] = []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(pred):
            score = _event_match_score(g, p)
            if score >= threshold:
                candidates.append((score, gi, pi))
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    used_g: set[int] = set()
    used_p: set[int] = set()
    mapping: dict[int, int] = {}
    for _score, gi, pi in candidates:
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        g_idx = gold[gi].get("indice", gi)
        p_idx = pred[pi].get("indice", pi)
        mapping[int(g_idx)] = int(p_idx)
    return mapping


def _field_equal(gold: Mapping[str, Any], pred: Mapping[str, Any], field: str) -> bool:
    gv = gold.get(field)
    pv = pred.get(field)
    if field == "lemma":
        return str(gv or "").strip().lower() == str(pv or "").strip().lower()
    return gv == pv


def score_attributes(
    gold_eventi: Sequence[Mapping[str, Any]] | None,
    pred_eventi: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    gold = _as_list(gold_eventi)
    pred = _as_list(pred_eventi)
    mapping = align_events(gold, pred)
    gold_by_idx = {int(e.get("indice", i)): e for i, e in enumerate(gold)}
    pred_by_idx = {int(e.get("indice", i)): e for i, e in enumerate(pred)}
    tp = len(mapping)
    fp = len(pred) - tp
    fn = len(gold) - tp
    overall = _prf(tp, fp, fn)
    pred_used = set(mapping.values())
    per_field: dict[str, dict[str, Any]] = {}
    for field in ATTRIBUTE_FIELDS:
        ftp = ffp = ffn = 0
        for g_idx, g in gold_by_idx.items():
            p_idx = mapping.get(g_idx)
            if p_idx is None:
                ffn += 1
                continue
            p = pred_by_idx.get(p_idx)
            if p is None:
                ffn += 1
                continue
            if _field_equal(g, p, field):
                ftp += 1
            else:
                ffn += 1
                ffp += 1
        for p_idx, _p in pred_by_idx.items():
            if p_idx not in pred_used:
                ffp += 1
        per_field[field] = _prf(ftp, ffp, ffn)
    overall["per_field"] = per_field
    return overall


def _rel_tuple(
    rel: Mapping[str, Any],
    index_map: Mapping[int, int] | None = None,
) -> tuple[int, int, str] | None:
    try:
        da = int(rel.get("da_indice"))
        a = int(rel.get("a_indice"))
    except (TypeError, ValueError):
        return None
    tipo = str(rel.get("tipo") or "")
    if not tipo:
        return None
    if index_map is not None:
        if da not in index_map or a not in index_map:
            return None
        da, a = int(index_map[da]), int(index_map[a])
    return (da, a, tipo)


def score_relations(
    gold_relazioni: Sequence[Mapping[str, Any]] | None,
    pred_relazioni: Sequence[Mapping[str, Any]] | None,
    gold_eventi: Sequence[Mapping[str, Any]] | None = None,
    pred_eventi: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    gold = _as_list(gold_relazioni)
    pred = _as_list(pred_relazioni)
    index_map: dict[int, int] | None = None
    if gold_eventi is not None or pred_eventi is not None:
        index_map = align_events(gold_eventi or [], pred_eventi or [])

    gold_keys: list[tuple[int, int, str]] = []
    gold_unmapped = 0
    for rel in gold:
        key = _rel_tuple(rel, index_map)
        if key is None:
            gold_unmapped += 1
        else:
            gold_keys.append(key)

    pred_keys: list[tuple[int, int, str]] = []
    for rel in pred:
        key = _rel_tuple(rel, None)
        if key is not None:
            pred_keys.append(key)

    gold_counter = Counter(gold_keys)
    pred_counter = Counter(pred_keys)
    tp = sum((gold_counter & pred_counter).values())
    fp = sum((pred_counter - gold_counter).values())
    fn = sum((gold_counter - pred_counter).values()) + gold_unmapped
    overall = _prf(tp, fp, fn)

    types = sorted({k[2] for k in gold_keys} | {k[2] for k in pred_keys})
    per_type: dict[str, dict[str, Any]] = {}
    for tipo in types:
        g_t = Counter({k: c for k, c in gold_counter.items() if k[2] == tipo})
        p_t = Counter({k: c for k, c in pred_counter.items() if k[2] == tipo})
        t_tp = sum((g_t & p_t).values())
        t_fp = sum((p_t - g_t).values())
        t_fn = sum((g_t - p_t).values())
        per_type[tipo] = _prf(t_tp, t_fp, t_fn)
    overall["per_type"] = per_type
    return overall


def score_item(gold: Mapping[str, Any] | None, pred: Mapping[str, Any] | None) -> dict[str, Any]:
    gold_inner = _inner_gold(gold)
    pred_inner = _inner_gold(pred)
    gold_eventi = _as_list(gold_inner.get("eventi"))
    pred_eventi = _as_list(pred_inner.get("eventi"))
    return {
        "segmentation": score_segmentation(
            gold_inner.get("sentences"), pred_inner.get("sentences")
        ),
        "attributes": score_attributes(gold_eventi, pred_eventi),
        "relations": score_relations(
            gold_inner.get("relazioni"),
            pred_inner.get("relazioni"),
            gold_eventi=gold_eventi,
            pred_eventi=pred_eventi,
        ),
    }


def _micro_from_parts(parts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = 0
    for part in parts:
        tp += int(part.get("tp") or 0)
        fp += int(part.get("fp") or 0)
        fn += int(part.get("fn") or 0)
    return _prf(tp, fp, fn)


def score_corpus(
    items: Sequence[Mapping[str, Any]] | None,
    predictions_by_id: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rows = _as_list(items)
    preds = dict(predictions_by_id or {})
    seg_parts: list[dict[str, Any]] = []
    attr_parts: list[dict[str, Any]] = []
    rel_parts: list[dict[str, Any]] = []
    field_parts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    type_parts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    n_gold_sentences = n_gold_events = n_gold_relations = 0
    n_pred_sentences = n_pred_events = n_pred_relations = 0
    n_scored = 0

    for item in rows:
        item_id = str(item.get("id") or "")
        gold_inner = _inner_gold(item)
        pred = preds.get(item_id)
        scored = score_item(gold_inner, pred)
        n_scored += 1
        n_gold_sentences += len(_as_list(gold_inner.get("sentences")))
        n_gold_events += len(_as_list(gold_inner.get("eventi")))
        n_gold_relations += len(_as_list(gold_inner.get("relazioni")))
        pred_inner = _inner_gold(pred)
        n_pred_sentences += len(_as_list(pred_inner.get("sentences")))
        n_pred_events += len(_as_list(pred_inner.get("eventi")))
        n_pred_relations += len(_as_list(pred_inner.get("relazioni")))
        seg_parts.append(scored["segmentation"])
        attr_parts.append(scored["attributes"])
        rel_parts.append(scored["relations"])
        for field, metrics in (scored["attributes"].get("per_field") or {}).items():
            field_parts[field].append(metrics)
        for tipo, metrics in (scored["relations"].get("per_type") or {}).items():
            type_parts[tipo].append(metrics)

    segmentation = _micro_from_parts(seg_parts)
    attributes = _micro_from_parts(attr_parts)
    attributes["per_field"] = {
        field: _micro_from_parts(parts) for field, parts in field_parts.items()
    }
    relations = _micro_from_parts(rel_parts)
    relations["per_type"] = {
        tipo: _micro_from_parts(parts) for tipo, parts in type_parts.items()
    }
    return {
        "n_items": n_scored,
        "counts": {
            "gold_sentences": n_gold_sentences,
            "gold_events": n_gold_events,
            "gold_relations": n_gold_relations,
            "pred_sentences": n_pred_sentences,
            "pred_events": n_pred_events,
            "pred_relations": n_pred_relations,
        },
        "segmentation": segmentation,
        "attributes": attributes,
        "relations": relations,
    }


def validate_item(item: Mapping[str, Any]) -> list[str]:
    """Return human-readable well-formedness errors (empty list = ok)."""
    errors: list[str] = []
    item_id = str(item.get("id") or "<missing-id>")
    lang = item.get("lang")
    if lang not in LANGS:
        errors.append(f"{item_id}: lang must be 'it' or 'en', got {lang!r}")
    text = item.get("text")
    if not isinstance(text, str) or not text:
        errors.append(f"{item_id}: text must be a non-empty string")
        return errors
    phenomena = item.get("phenomena")
    if not isinstance(phenomena, list) or not all(isinstance(p, str) and p for p in phenomena):
        errors.append(f"{item_id}: phenomena must be a non-empty list of strings")

    gold = _inner_gold(item)
    sentences = _as_list(gold.get("sentences"))
    eventi = _as_list(gold.get("eventi"))
    relazioni = _as_list(gold.get("relazioni"))
    if not sentences:
        errors.append(f"{item_id}: gold.sentences is empty")

    for i, sent in enumerate(sentences):
        start = sent.get("offset_inizio")
        end = sent.get("offset_fine")
        testo = sent.get("testo")
        tipo = sent.get("tipo")
        indice = sent.get("indice", i)
        if tipo not in SENTENCE_TIPI:
            errors.append(f"{item_id}: sentence {indice} tipo invalid: {tipo!r}")
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"{item_id}: sentence {indice} offsets must be int")
            continue
        if not (0 <= start < end <= len(text)):
            errors.append(
                f"{item_id}: sentence {indice} offsets {start}:{end} out of range 0:{len(text)}"
            )
            continue
        slice_ = text[start:end]
        if testo != slice_:
            errors.append(
                f"{item_id}: sentence {indice} testo != text[{start}:{end}]"
            )

    seen_idx: set[int] = set()
    for i, ev in enumerate(eventi):
        idx = ev.get("indice", i)
        if idx in seen_idx:
            errors.append(f"{item_id}: duplicate event indice {idx}")
        seen_idx.add(idx)
        span = ev.get("span")
        if not isinstance(span, str) or not span:
            errors.append(f"{item_id}: event {idx} missing span")
        elif span not in text:
            errors.append(f"{item_id}: event {idx} span not found in text: {span!r}")
        else:
            ev_start = ev.get("offset_inizio")
            ev_end = ev.get("offset_fine")
            if isinstance(ev_start, int) and isinstance(ev_end, int):
                if not (0 <= ev_start < ev_end <= len(text)):
                    errors.append(f"{item_id}: event {idx} offsets out of range")
                elif text[ev_start:ev_end] != span:
                    errors.append(
                        f"{item_id}: event {idx} span != text[{ev_start}:{ev_end}]"
                    )
            in_sentence = False
            for sent in sentences:
                start = sent.get("offset_inizio")
                end = sent.get("offset_fine")
                if isinstance(start, int) and isinstance(end, int) and span in text[start:end]:
                    in_sentence = True
                    break
            if sentences and not in_sentence:
                errors.append(f"{item_id}: event {idx} span not inside any sentence")
        if ev.get("tempo") not in TEMPI:
            errors.append(f"{item_id}: event {idx} tempo invalid: {ev.get('tempo')!r}")
        if ev.get("modalita") not in MODALITA:
            errors.append(f"{item_id}: event {idx} modalita invalid: {ev.get('modalita')!r}")
        if ev.get("fattualita") not in FATTUALITA:
            errors.append(f"{item_id}: event {idx} fattualita invalid: {ev.get('fattualita')!r}")
        if not isinstance(ev.get("e_testa"), bool):
            errors.append(f"{item_id}: event {idx} e_testa must be bool")
        if ev.get("sogg_speciale") not in SOGG_SPECIALI:
            errors.append(f"{item_id}: event {idx} sogg_speciale invalid")
        for arg in _as_list(ev.get("argomenti")):
            if arg.get("ruolo") not in RUOLI:
                errors.append(f"{item_id}: event {idx} arg ruolo invalid: {arg.get('ruolo')!r}")
            if arg.get("tipo_superficiale") not in TIPI_SUPERFICIALI:
                tipo_s = arg.get("tipo_superficiale")
                errors.append(
                    f"{item_id}: event {idx} tipo_superficiale invalid: {tipo_s!r}"
                )
            forma = arg.get("forma")
            if arg.get("tipo_superficiale") != "sogg_nullo" and (
                not isinstance(forma, str) or not forma
            ):
                errors.append(f"{item_id}: event {idx} arg forma empty")

    event_indices = {int(e.get("indice", i)) for i, e in enumerate(eventi)}
    for i, rel in enumerate(relazioni):
        try:
            da = int(rel.get("da_indice"))
            a = int(rel.get("a_indice"))
        except (TypeError, ValueError):
            errors.append(f"{item_id}: relation {i} da/a not int")
            continue
        if da not in event_indices or a not in event_indices:
            errors.append(f"{item_id}: relation {i} indices {da}->{a} missing")
        tipo = rel.get("tipo")
        if not isinstance(tipo, str) or not tipo:
            errors.append(f"{item_id}: relation {i} tipo missing")
        allen = rel.get("relazione_allen")
        if allen is not None and allen not in ALLEN_RELATIONS:
            errors.append(f"{item_id}: relation {i} relazione_allen invalid: {allen!r}")
        conf = rel.get("confidenza")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            errors.append(f"{item_id}: relation {i} confidenza must be float")
        if not isinstance(rel.get("intra_frase"), bool):
            errors.append(f"{item_id}: relation {i} intra_frase must be bool")
    return errors


def validate_corpus(items: Sequence[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids: list[str] = []
    for item in items:
        ids.append(str(item.get("id") or ""))
        errors.extend(validate_item(item))
    if len(ids) != len(set(ids)):
        errors.append("duplicate item ids")
    return errors
