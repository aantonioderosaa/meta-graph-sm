"""Bare grammatical heads for extracted entities (SOGG / OGG / TEMPO).

The node name is the head noun or proper name, without articles, adjectives,
numbers, or adverbs. Those modifiers stay in the per-occurrence summary.
Identity is the cleaned grammatical form: two "garage" mentions, even when
they mean different things, share one instance.

Isolation D6: ``app.pipeline.event_graph.*`` and ``app.models.event_graph``.
"""

from __future__ import annotations

import re
from typing import Any

from app.pipeline.event_graph.text_norm import (
    _DETERMINERS,
    _DISCOURSE_ADVERBS,
    fold_text,
)

_TOKEN = re.compile(
    r"[A-Za-zÀ-ÿ]+(?:['’][A-Za-zÀ-ÿ]+)?|\d+[./:\-]?\d*(?:[./:\-]\d+)*"
)

_MESI = frozenset(
    {
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "gen",
        "feb",
        "mar",
        "apr",
        "mag",
        "giu",
        "lug",
        "ago",
        "set",
        "ott",
        "nov",
        "dic",
        "jan",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "dec",
    }
)
_UNITA_TEMPO = frozenset(
    {
        "minuto",
        "minuti",
        "minute",
        "minutes",
        "ora",
        "ore",
        "hour",
        "hours",
        "secondo",
        "secondi",
        "second",
        "seconds",
        "giorno",
        "giorni",
        "day",
        "days",
        "settimana",
        "settimane",
        "week",
        "weeks",
        "mese",
        "mesi",
        "month",
        "months",
        "anno",
        "anni",
        "year",
        "years",
        "mezzogiorno",
        "mezzanotte",
        "vigilia",
        "natale",
        "christmas",
        "ieri",
        "oggi",
        "domani",
        "yesterday",
        "today",
        "tomorrow",
        "sera",
        "mattina",
        "pomeriggio",
        "notte",
        "estate",
        "inverno",
        "primavera",
        "autunno",
        "morning",
        "evening",
        "night",
        "afternoon",
        "summer",
        "winter",
        "spring",
        "autumn",
        "fall",
        "deadline",
        "scadenza",
        "entro",
        "fa",
        "ago",
        "alle",
        "all",
        "dal",
        "al",
    }
)
_PAROLE_NUMERO = frozenset(
    {
        "zero",
        "un",
        "uno",
        "una",
        "due",
        "tre",
        "quattro",
        "cinque",
        "sei",
        "sette",
        "otto",
        "nove",
        "dieci",
        "undici",
        "dodici",
        "tredici",
        "quattordici",
        "quindici",
        "sedici",
        "diciassette",
        "diciotto",
        "diciannove",
        "venti",
        "trenta",
        "quaranta",
        "cinquanta",
        "sessanta",
        "settanta",
        "ottanta",
        "novanta",
        "cento",
        "mille",
        "mila",
        "milione",
        "milioni",
        "primo",
        "prima",
        "secondi",
        "secondo",
        "seconda",
        "terzo",
        "terza",
        "quarto",
        "quarta",
        "quinto",
        "quinta",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "first",
        "second",
        "third",
        "fourth",
        "fifth",
        "a",
        "an",
    }
)
_DIMOSTRATIVI = frozenset(
    {
        "questo",
        "questa",
        "questi",
        "queste",
        "codesto",
        "codesta",
        "codesti",
        "codeste",
        "quello",
        "quella",
        "quelli",
        "quelle",
        "quel",
        "quei",
        "quegli",
        "stesso",
        "stessa",
        "stessi",
        "stesse",
        "medesimo",
        "medesima",
        "medesimi",
        "medesime",
        "this",
        "that",
        "these",
        "those",
        "same",
        "such",
    }
)
_POSSESSIVI = frozenset(
    {
        "mio",
        "mia",
        "miei",
        "mie",
        "tuo",
        "tua",
        "tuoi",
        "tue",
        "suo",
        "sua",
        "suoi",
        "sue",
        "nostro",
        "nostra",
        "nostri",
        "nostre",
        "vostro",
        "vostra",
        "vostri",
        "vostre",
        "loro",
        "my",
        "your",
        "his",
        "her",
        "its",
        "our",
        "their",
    }
)
_QUANTIFICATORI = frozenset(
    {
        "ogni",
        "qualche",
        "qualsiasi",
        "qualunque",
        "alcun",
        "alcuno",
        "alcuna",
        "alcuni",
        "alcune",
        "certo",
        "certa",
        "certi",
        "certe",
        "tutto",
        "tutta",
        "tutti",
        "tutte",
        "altro",
        "altra",
        "altri",
        "altre",
        "molto",
        "molta",
        "molti",
        "molte",
        "poco",
        "poca",
        "pochi",
        "poche",
        "tanto",
        "tanta",
        "tanti",
        "tante",
        "troppo",
        "troppa",
        "troppi",
        "troppe",
        "parecchio",
        "parecchia",
        "parecchi",
        "parecchie",
        "nessun",
        "nessuno",
        "nessuna",
        "each",
        "every",
        "all",
        "any",
        "some",
        "other",
        "another",
        "many",
        "much",
        "few",
        "several",
        "both",
        "no",
        "none",
    }
)
_PREPOSIZIONI = frozenset(
    {
        "di",
        "da",
        "a",
        "ad",
        "in",
        "su",
        "con",
        "per",
        "tra",
        "fra",
        "come",
        "of",
        "from",
        "to",
        "at",
        "on",
        "with",
        "for",
        "by",
        "as",
        "into",
        "onto",
        "over",
        "under",
        "about",
        *_DETERMINERS,
        "alle",
        "ai",
        "agli",
        "dei",
        "degli",
        "delle",
        "dai",
        "dagli",
        "dalle",
        "nei",
        "negli",
        "nelle",
        "sui",
        "sugli",
        "sulle",
        "coi",
        "cui",
    }
)


def _inflessioni_it(lemmas: tuple[str, ...]) -> frozenset[str]:
    out: set[str] = set()
    for lemma in lemmas:
        lemma = lemma.casefold()
        out.add(lemma)
        if lemma.endswith("o") and len(lemma) > 2:
            base = lemma[:-1]
            out.update({base + "a", base + "i", base + "e"})
        elif lemma.endswith("e") and len(lemma) > 2:
            out.add(lemma[:-1] + "i")
    return frozenset(out)


_AGGETTIVI = _inflessioni_it(
    (
        "vecchio",
        "giovane",
        "nuovo",
        "grande",
        "piccolo",
        "bello",
        "brutto",
        "buono",
        "cattivo",
        "lungo",
        "corto",
        "alto",
        "basso",
        "povero",
        "ricco",
        "freddo",
        "caldo",
        "solo",
        "unico",
        "intero",
        "vero",
        "falso",
        "pieno",
        "vuoto",
        "morto",
        "vivo",
        "aperto",
        "chiuso",
        "rotto",
        "semplice",
        "difficile",
        "facile",
        "importante",
        "ultimo",
        "prossimo",
        "scorso",
        "passato",
        "presente",
        "locale",
        "nazionale",
        "pubblico",
        "privato",
        "antico",
        "moderno",
        "rapido",
        "lento",
        "forte",
        "debole",
        "anziano",
        "oscuro",
        "chiaro",
        "stretto",
        "largo",
        "profondo",
        "abbandonato",
        "smarrito",
        "perduto",
        "trovato",
        "nascosto",
        "ignoto",
        "noto",
        "famoso",
        "comune",
        "raro",
        "normale",
        "strano",
        "stesso",
        "medesimo",
        "generale",
        "speciale",
        "principale",
        "secondario",
        "centrale",
        "destro",
        "sinistro",
        "interno",
        "esterno",
        "vicino",
        "lontano",
        "libero",
        "occupato",
        "vuoto",
        "sporco",
        "pulito",
        "secco",
        "umido",
        "duro",
        "molle",
        "pesante",
        "leggero",
        "dolce",
        "amaro",
        "salato",
        "silenzioso",
        "rumoroso",
        "nascosto",
        "visibile",
        "invisibile",
        "possibile",
        "impossibile",
        "necessario",
        "inutile",
        "utile",
        "pericoloso",
        "sicuro",
        "stanco",
        "felice",
        "triste",
        "arrabbiato",
        "calmo",
        "nervoso",
        "malato",
        "sano",
        "intero",
        "mezzo",
        "doppio",
        "triplo",
        "numeroso",
        "vario",
        "diverso",
        "simile",
        "uguale",
        "proprio",
        "altrui",
        "detto",
        "suddetto",
        "citato",
        "menzionato",
        "relativo",
        "assoluto",
        "temporale",
        "spaziale",
        "fisico",
        "antico",
        "recente",
        "attuale",
        "futuro",
        "originario",
        "originale",
        "finto",
        "vero",
        "reale",
        "ipotetico",
        "presunto",
        "cosiddetto",
        "suddetto",
    )
) | frozenset(
    {
        "old",
        "young",
        "new",
        "great",
        "big",
        "small",
        "little",
        "beautiful",
        "ugly",
        "good",
        "bad",
        "long",
        "short",
        "tall",
        "poor",
        "rich",
        "cold",
        "hot",
        "warm",
        "only",
        "unique",
        "entire",
        "whole",
        "true",
        "false",
        "full",
        "empty",
        "dead",
        "alive",
        "open",
        "closed",
        "broken",
        "simple",
        "easy",
        "important",
        "last",
        "next",
        "former",
        "latter",
        "past",
        "present",
        "current",
        "local",
        "public",
        "private",
        "ancient",
        "modern",
        "fast",
        "slow",
        "strong",
        "weak",
        "abandoned",
        "lost",
        "hidden",
        "unknown",
        "famous",
        "common",
        "rare",
        "normal",
        "strange",
        "same",
        "general",
        "special",
        "main",
        "central",
        "right",
        "left",
        "inner",
        "outer",
        "near",
        "far",
        "free",
        "busy",
        "dirty",
        "clean",
        "dry",
        "wet",
        "hard",
        "light",
        "heavy",
        "sweet",
        "bitter",
        "silent",
        "loud",
        "visible",
        "possible",
        "necessary",
        "useful",
        "useless",
        "dangerous",
        "safe",
        "tired",
        "happy",
        "sad",
        "angry",
        "calm",
        "sick",
        "healthy",
        "double",
        "various",
        "different",
        "similar",
        "equal",
        "own",
        "so-called",
        "real",
        "recent",
        "original",
        "deep",
        "wide",
        "narrow",
        "large",
        "dark",
    }
)
_AVVERBI = frozenset(
    {
        "molto",
        "poco",
        "già",
        "gia",
        "ancora",
        "sempre",
        "mai",
        "poi",
        "quasi",
        "circa",
        "davvero",
        "proprio",
        "solo",
        "soltanto",
        "appena",
        "subito",
        "ora",
        "adesso",
        "allora",
        "così",
        "cosi",
        "più",
        "piu",
        "meno",
        "meglio",
        "peggio",
        "insieme",
        "oltre",
        "invece",
        "però",
        "pero",
        "quindi",
        "inoltre",
        "pure",
        "anche",
        "neanche",
        "nemmeno",
        "ben",
        "bene",
        "male",
        "forse",
        "probabilmente",
        "certamente",
        "sicuramente",
        "lentamente",
        "rapidamente",
        "very",
        "really",
        "already",
        "still",
        "always",
        "never",
        "then",
        "almost",
        "about",
        "quite",
        "just",
        "only",
        "soon",
        "now",
        "here",
        "there",
        "too",
        "also",
        "even",
        "else",
        "so",
        "well",
        "badly",
        "perhaps",
        "probably",
        "slowly",
        "quickly",
        "together",
        *_DISCOURSE_ADVERBS,
    }
)
_PRONOMI = frozenset(
    {
        "io",
        "tu",
        "lui",
        "lei",
        "egli",
        "ella",
        "esso",
        "essa",
        "essi",
        "esse",
        "noi",
        "voi",
        "loro",
        "mi",
        "ti",
        "ci",
        "vi",
        "si",
        "lo",
        "la",
        "li",
        "le",
        "gli",
        "ne",
        "me",
        "te",
        "sé",
        "se",
        "che",
        "chi",
        "cui",
        "chiunque",
        "quanto",
        "i",
        "he",
        "she",
        "it",
        "we",
        "you",
        "they",
        "him",
        "her",
        "them",
        "us",
        "me",
        "who",
        "whom",
        "which",
        "that",
        "this",
        "these",
        "those",
        "himself",
        "herself",
        "itself",
        "themselves",
        "somebody",
        "someone",
        "something",
        "anybody",
        "anyone",
        "anything",
        "everybody",
        "everyone",
        "everything",
        "nobody",
        "nothing",
    }
)
_PARTICIPIO_AGG = re.compile(
    r".{3,}(?:ato|ata|ati|ate|uto|uta|uti|ute|ito|ita|iti|ite)$",
    re.IGNORECASE,
)
_ISO_TOKEN = re.compile(r"^\d{1,4}(?:[./:\-]\d{1,4})+$")
_SOLO_CIFRE = re.compile(r"^\d+$")


def nome_grezzo(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    name = getattr(item, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return ""


def summary_grezzo(item: Any) -> str:
    if isinstance(item, str):
        return ""
    summary = getattr(item, "summary", None)
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    if isinstance(item, dict):
        return str(item.get("summary") or "").strip()
    return ""


_PREP_SN = frozenset(
    {
        "di",
        "da",
        "a",
        "ad",
        "in",
        "su",
        "con",
        "per",
        "tra",
        "fra",
        "of",
        "from",
        "to",
        "at",
        "on",
        "with",
        "for",
        "by",
        "as",
        "into",
        "onto",
        "over",
        "under",
        "nel",
        "nello",
        "nella",
        "nei",
        "negli",
        "nelle",
        "del",
        "dello",
        "della",
        "dei",
        "degli",
        "delle",
        "al",
        "allo",
        "alla",
        "ai",
        "agli",
        "alle",
        "dal",
        "dallo",
        "dalla",
        "dai",
        "dagli",
        "dalle",
        "sul",
        "sullo",
        "sulla",
        "sui",
        "sugli",
        "sulle",
    }
)


def _token_spans(testo: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in _TOKEN.finditer(testo or "")]


def _e_numero(token: str) -> bool:
    testo = token.casefold()
    if _SOLO_CIFRE.fullmatch(testo):
        return True
    if testo in _PAROLE_NUMERO:
        return True
    return False


def _e_temporale_lessico(token: str) -> bool:
    testo = token.casefold()
    if testo in _MESI or testo in _UNITA_TEMPO:
        return True
    if _ISO_TOKEN.fullmatch(token):
        return True
    return False


def _e_avverbio(token: str) -> bool:
    testo = token.casefold()
    if testo in _AVVERBI:
        return True
    return testo.endswith("mente") or testo.endswith("ly")


def _e_aggettivo(token: str, *, unico: bool) -> bool:
    testo = token.casefold()
    if testo in _AGGETTIVI or testo in _DIMOSTRATIVI:
        return True
    if not unico and _PARTICIPIO_AGG.fullmatch(testo):
        return True
    return False


def _e_modificatore(
    token: str,
    *,
    temporale: bool,
    unico: bool,
) -> bool:
    if _e_proprio(token):
        return False
    testo = token.casefold()
    if testo in _PRONOMI and unico:
        return False
    if testo in _POSSESSIVI or testo in _QUANTIFICATORI or testo in _DIMOSTRATIVI:
        return True
    if testo in _DISCOURSE_ADVERBS:
        return True
    if _e_avverbio(token):
        return True
    if _e_aggettivo(token, unico=unico):
        return True
    if _e_numero(token):
        return not temporale
    if testo in _PREPOSIZIONI and not temporale:
        return True
    return False


def _e_proprio(token: str) -> bool:
    if not token or not token[0].isalpha():
        return False
    return token[0].isupper() and token.casefold() not in _PREPOSIZIONI


def _span_temporale(tokens: list[str]) -> bool:
    return any(_e_temporale_lessico(tok) or _ISO_TOKEN.fullmatch(tok) for tok in tokens)


def _strip_determiners_leading(testo: str) -> str:
    grezzo = testo
    precedente = None
    while precedente != grezzo:
        precedente = grezzo
        lower = grezzo.casefold()
        for det in _DETERMINERS:
            if not lower.startswith(det):
                continue
            rest = grezzo[len(det) :]
            if det.endswith("'") or not rest or rest[0].isspace():
                grezzo = rest.lstrip()
                break
    return grezzo


def pulisci_forma(nome: str | None, *, temporale: bool = False) -> str:
    """Bare head: no articles, adjectives, adverbs; numbers stay only on TEMPO."""
    grezzo = _strip_determiners_leading(fold_text(nome or "").strip())
    if not grezzo:
        return ""
    spans = _token_spans(grezzo)
    if not spans:
        return ""
    tokens = [span[2] for span in spans]
    if len(tokens) == 1 and tokens[0].casefold() in _PRONOMI:
        return tokens[0]
    temporale_ok = temporale or _span_temporale(tokens)
    left = 0
    right = len(tokens) - 1
    while left < right and _e_modificatore(
        tokens[left], temporale=temporale_ok, unico=False
    ):
        left += 1
    while right > left and _e_modificatore(
        tokens[right], temporale=temporale_ok, unico=False
    ):
        right -= 1
    tenuti = [
        token
        for token in tokens[left : right + 1]
        if temporale_ok or token.casefold() not in _PREPOSIZIONI or _e_proprio(token)
    ]
    if not tenuti:
        if temporale_ok:
            return " ".join(tokens)
        return tokens[-1]
    if temporale_ok:
        return " ".join(tenuti)
    if all(_e_proprio(token) for token in tenuti):
        return " ".join(tenuti)
    if (
        len(tenuti) >= 2
        and not any(_e_proprio(token) for token in tenuti)
        and all(any(ch.isalpha() for ch in token) for token in tenuti)
        and all(token.casefold() not in _AGGETTIVI for token in tenuti)
        and not any(_e_numero(token) or _e_avverbio(token) for token in tenuti)
    ):
        return " ".join(tenuti)
    return tenuti[0]


def forma_identita(nome: str | None) -> str:
    """Casefolded identity key of the cleaned grammatical head."""
    pulita = pulisci_forma(nome)
    if pulita:
        return pulita.casefold()
    folded = fold_text(nome or "").casefold()
    for mark in (",", ";", ":", "!", "?"):
        folded = folded.replace(mark, " ")
    tokens = [
        tok
        for tok in folded.split()
        if tok and tok not in _DISCOURSE_ADVERBS
    ]
    return " ".join(tokens)


def e_entita_ammessa(nome: str | None, *, temporale: bool = False) -> bool:
    grezzo = fold_text(nome or "").strip()
    if not grezzo:
        return False
    tokens = [span[2] for span in _token_spans(grezzo)]
    if not tokens:
        return False
    if len(tokens) == 1 and tokens[0].casefold() in _PRONOMI:
        return True
    if temporale:
        return bool(pulisci_forma(grezzo, temporale=True))
    if len(tokens) == 1 and (
        _e_numero(tokens[0])
        or _e_avverbio(tokens[0])
        or _e_aggettivo(tokens[0], unico=True)
        or tokens[0].casefold() in _DIMOSTRATIVI
        or tokens[0].casefold() in _QUANTIFICATORI
        or tokens[0].casefold() in _POSSESSIVI
    ):
        return False
    return bool(pulisci_forma(grezzo, temporale=False))


def _troppo_ampio(summary: str, evento_testo: str, testa: str) -> bool:
    s = " ".join(summary.split())
    e = " ".join((evento_testo or "").split())
    if not s:
        return True
    if e and s.casefold() == e.casefold():
        return True
    if e and len(s) > max(80, int(len(e) * 0.8)) and testa.casefold() in s.casefold():
        return True
    return False


def _espandi_contesto(evento_testo: str, testa: str) -> str:
    testo = fold_text(evento_testo or "")
    testa_ok = fold_text(testa or "").strip()
    if not testo or not testa_ok:
        return testa_ok
    spans = _token_spans(testo)
    if not spans:
        return testa_ok
    testa_toks = [span[2].casefold() for span in _token_spans(testa_ok)]
    if not testa_toks:
        return testa_ok
    start = None
    for i in range(len(spans) - len(testa_toks) + 1):
        finestra = [spans[i + j][2].casefold() for j in range(len(testa_toks))]
        if finestra == testa_toks:
            start = i
            break
    if start is None:
        idx = testo.casefold().find(testa_ok.casefold())
        if idx < 0:
            return testa_ok
        return testo[idx : idx + len(testa_ok)]
    end = start + len(testa_toks) - 1
    left = start
    while left > 0:
        prev = spans[left - 1][2]
        if prev.casefold() in _PREP_SN:
            left -= 1
            break
        if _e_modificatore(prev, temporale=False, unico=False):
            left -= 1
            continue
        break
    right = end
    while right + 1 < len(spans):
        nxt = spans[right + 1][2]
        if nxt.casefold() in _PREP_SN:
            break
        if _e_modificatore(nxt, temporale=False, unico=False):
            right += 1
            continue
        break
    return testo[spans[left][0] : spans[right][1]]


def contesto_entita(
    evento_testo: str,
    grezza: str,
    pulita: str,
    summary_llm: str = "",
) -> str:
    """Local NP that contextualises the entity — never the whole event."""
    llm = " ".join((summary_llm or "").split())
    pulita_ok = " ".join(fold_text(pulita or "").split())
    grezza_ok = " ".join(fold_text(grezza or "").split())
    if llm and not _troppo_ampio(llm, evento_testo, pulita_ok):
        return llm
    espanso = _espandi_contesto(evento_testo, pulita_ok or grezza_ok)
    if espanso and not _troppo_ampio(espanso, evento_testo, pulita_ok):
        return espanso
    if grezza_ok and pulita_ok and grezza_ok.casefold() != pulita_ok.casefold():
        if not _troppo_ampio(grezza_ok, evento_testo, pulita_ok):
            return grezza_ok
    return pulita_ok or grezza_ok


__all__ = [
    "contesto_entita",
    "e_entita_ammessa",
    "forma_identita",
    "nome_grezzo",
    "pulisci_forma",
    "summary_grezzo",
]
