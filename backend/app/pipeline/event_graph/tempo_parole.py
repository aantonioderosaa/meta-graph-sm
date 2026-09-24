"""Italian/English number words, spoken clocks, and spoken calendar dates.

Pure functions. No I/O. Used by ``tempo_iso`` so a TEMPO span written in
words occupies the same date/ora entity as its digit form.

Isolation D6: ``app.pipeline.event_graph.*`` and ``app.models.event_graph``.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

_VOCALI: Final[set[str]] = set("aeiou")
_ARTICOLI: Final[frozenset[str]] = frozenset(
    {
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "the",
        "del",
        "dello",
        "della",
        "dei",
        "degli",
        "delle",
        "di",
        "of",
        "nel",
        "nello",
        "nella",
        "in",
        "a",
        "ad",
        "al",
        "allo",
        "alla",
    }
)
_UNI: Final[dict[int, tuple[str, ...]]] = {
    0: ("zero",),
    1: ("uno", "un", "una"),
    2: ("due",),
    3: ("tre",),
    4: ("quattro",),
    5: ("cinque",),
    6: ("sei",),
    7: ("sette",),
    8: ("otto",),
    9: ("nove",),
    10: ("dieci",),
    11: ("undici",),
    12: ("dodici",),
    13: ("tredici",),
    14: ("quattordici",),
    15: ("quindici",),
    16: ("sedici",),
    17: ("diciassette",),
    18: ("diciotto",),
    19: ("diciannove",),
}
_DECINE: Final[dict[int, str]] = {
    20: "venti",
    30: "trenta",
    40: "quaranta",
    50: "cinquanta",
    60: "sessanta",
    70: "settanta",
    80: "ottanta",
    90: "novanta",
}
_UNI_EN: Final[dict[int, tuple[str, ...]]] = {
    0: ("zero",),
    1: ("one",),
    2: ("two",),
    3: ("three",),
    4: ("four",),
    5: ("five",),
    6: ("six",),
    7: ("seven",),
    8: ("eight",),
    9: ("nine",),
    10: ("ten",),
    11: ("eleven",),
    12: ("twelve",),
    13: ("thirteen",),
    14: ("fourteen",),
    15: ("fifteen",),
    16: ("sixteen",),
    17: ("seventeen",),
    18: ("eighteen",),
    19: ("nineteen",),
}
_DECINE_EN: Final[dict[int, str]] = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}

_ORDINALI: Final[dict[str, int]] = {
    "primo": 1,
    "prima": 1,
    "secondo": 2,
    "seconda": 2,
    "terzo": 3,
    "terza": 3,
    "quarto": 4,
    "quarta": 4,
    "quinto": 5,
    "quinta": 5,
    "sesto": 6,
    "sesta": 6,
    "settimo": 7,
    "settima": 7,
    "ottavo": 8,
    "ottava": 8,
    "nono": 9,
    "nona": 9,
    "decimo": 10,
    "decima": 10,
    "undicesimo": 11,
    "undicesima": 11,
    "dodicesimo": 12,
    "dodicesima": 12,
    "tredicesimo": 13,
    "tredicesima": 13,
    "quattordicesimo": 14,
    "quattordicesima": 14,
    "quindicesimo": 15,
    "quindicesima": 15,
    "sedicesimo": 16,
    "sedicesima": 16,
    "diciassettesimo": 17,
    "diciassettesima": 17,
    "diciottesimo": 18,
    "diciottesima": 18,
    "diciannovesimo": 19,
    "diciannovesima": 19,
    "ventesimo": 20,
    "ventesima": 20,
    "ventunesimo": 21,
    "ventunesima": 21,
    "ventiduesimo": 22,
    "ventiduesima": 22,
    "ventitreesimo": 23,
    "ventitreesima": 23,
    "ventiquattresimo": 24,
    "ventiquattresima": 24,
    "venticinquesimo": 25,
    "venticinquesima": 25,
    "ventiseiesimo": 26,
    "ventiseiesima": 26,
    "ventisettesimo": 27,
    "ventisettesima": 27,
    "ventottesimo": 28,
    "ventottesima": 28,
    "ventinovesimo": 29,
    "ventinovesima": 29,
    "trentesimo": 30,
    "trentesima": 30,
    "trentunesimo": 31,
    "trentunesima": 31,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
    "twentyfirst": 21,
    "twentysecond": 22,
    "twentythird": 23,
    "twentyfourth": 24,
    "twentyfifth": 25,
    "twentysixth": 26,
    "twentyseventh": 27,
    "twentyeighth": 28,
    "twentyninth": 29,
    "thirtieth": 30,
    "thirtyfirst": 31,
}

_OREFICE: Final[dict[str, tuple[int, int, int, str]]] = {
    "mezzogiorno": (12, 0, 0, "minuto"),
    "mezzanotte": (0, 0, 0, "minuto"),
    "noon": (12, 0, 0, "minuto"),
    "midnight": (0, 0, 0, "minuto"),
}


class OrarioParole(NamedTuple):
    ora: int
    minuto: int
    secondo: int
    precisione: str


def _togli_accenti(testo: str) -> str:
    return (
        testo.replace("à", "a")
        .replace("è", "e")
        .replace("é", "e")
        .replace("ì", "i")
        .replace("í", "i")
        .replace("ò", "o")
        .replace("ó", "o")
        .replace("ù", "u")
        .replace("ú", "u")
    )


def _compatta(testo: str) -> str:
    pulito = _togli_accenti(testo.casefold())
    pulito = pulito.replace("'", "").replace("’", "").replace("-", "")
    return re.sub(r"[^a-z0-9]+", "", pulito)


def _unisci(sinistra: str, destra: str) -> str:
    if not sinistra:
        return destra
    if not destra:
        return sinistra
    if sinistra[-1] in _VOCALI and destra[0] in _VOCALI:
        return sinistra[:-1] + destra
    return sinistra + destra


def _aggiungi(mappa: dict[str, int], parola: str, valore: int) -> None:
    if not parola:
        return
    chiave = _compatta(parola)
    if chiave and chiave not in mappa:
        mappa[chiave] = valore


def _forme_0_99(n: int) -> list[str]:
    if n < 20:
        return [item for item in _UNI[n] if item not in {"un", "una"} or n == 1]
    decine, unita = divmod(n, 10)
    base = _DECINE[decine * 10]
    if unita == 0:
        return [base]
    fuori: list[str] = []
    for pezzo in _UNI[unita]:
        if pezzo in {"un", "una"}:
            continue
        fuori.append(_unisci(base, pezzo))
        concatenato = base + pezzo
        if concatenato not in fuori:
            fuori.append(concatenato)
    return fuori


def _forme_0_99_en(n: int) -> list[str]:
    if n < 20:
        return list(_UNI_EN[n])
    decine, unita = divmod(n, 10)
    base = _DECINE_EN[decine * 10]
    if unita == 0:
        return [base]
    return [base + _UNI_EN[unita][0], base + "-" + _UNI_EN[unita][0]]


def _forme_0_999(n: int) -> list[str]:
    if n < 100:
        return _forme_0_99(n)
    centinaia, resto = divmod(n, 100)
    if centinaia == 1:
        cento = "cento"
    else:
        cento = _UNI[centinaia][0] + "cento"
    if resto == 0:
        return [cento]
    fuori: list[str] = []
    for coda in _forme_0_99(resto):
        fuori.append(_unisci(cento, coda))
        concatenato = cento + coda
        if concatenato not in fuori:
            fuori.append(concatenato)
    return fuori


def _costruisci_mappa() -> dict[str, int]:
    mappa: dict[str, int] = {}
    for n in range(0, 100):
        for forma in _forme_0_99(n):
            _aggiungi(mappa, forma, n)
        for forma in _forme_0_99_en(n):
            _aggiungi(mappa, forma, n)
        if n == 1:
            _aggiungi(mappa, "un", 1)
            _aggiungi(mappa, "una", 1)
            _aggiungi(mappa, "a", 1)
            _aggiungi(mappa, "an", 1)
    for n in range(100, 1000):
        for forma in _forme_0_999(n):
            _aggiungi(mappa, forma, n)
    _aggiungi(mappa, "mille", 1000)
    _aggiungi(mappa, "thousand", 1000)
    for n in range(1, 1000):
        for coda in _forme_0_999(n):
            _aggiungi(mappa, _unisci("mille", coda), 1000 + n)
            _aggiungi(mappa, "mille" + coda, 1000 + n)
    for migliaia in range(2, 10):
        testa = _UNI[migliaia][0] + "mila"
        _aggiungi(mappa, testa, migliaia * 1000)
        for n in range(1, 1000):
            for coda in _forme_0_999(n):
                _aggiungi(mappa, _unisci(testa, coda), migliaia * 1000 + n)
                _aggiungi(mappa, testa + coda, migliaia * 1000 + n)
        testa_en = _UNI_EN[migliaia][0] + "thousand"
        _aggiungi(mappa, testa_en, migliaia * 1000)
    return mappa


_MAPPA: Final[dict[str, int]] = _costruisci_mappa()

_ORE_FORME: Final[dict[str, int]] = {}
for _n in range(0, 24):
    for _forma in _forme_0_99(_n):
        if _forma in {"un", "una", "a", "an"}:
            continue
        _ORE_FORME[_compatta(_forma)] = _n
    for _forma in _forme_0_99_en(_n):
        _ORE_FORME[_compatta(_forma)] = _n
_ORE_FORME["una"] = 1
_ORE_FORME["one"] = 1

_MINUTI_FORME: Final[dict[str, int]] = {}
for _n in range(0, 60):
    for _forma in _forme_0_99(_n):
        _MINUTI_FORME[_compatta(_forma)] = _n
    for _forma in _forme_0_99_en(_n):
        _MINUTI_FORME[_compatta(_forma)] = _n
_MINUTI_FORME["unquarto"] = 15
_MINUTI_FORME["quarto"] = 15
_MINUTI_FORME["quarter"] = 15
_MINUTI_FORME["mezzo"] = 30
_MINUTI_FORME["mezza"] = 30
_MINUTI_FORME["half"] = 30

_ORA_ALT: Final[str] = "|".join(
    sorted({forma for forma in _ORE_FORME if forma.isalpha()}, key=len, reverse=True)
)
_MIN_ALT: Final[str] = "|".join(
    sorted({forma for forma in _MINUTI_FORME if forma.isalpha()}, key=len, reverse=True)
)

_ORA_PAROLE: Final[re.Pattern[str]] = re.compile(
    rf"""
    \b(?:
        (?P<lessico>mezzogiorno|mezzanotte|noon|midnight)
        |
        (?:(?:alle|all['’]?|ore|at)\s*|(?:l['’]))
        (?P<ora_parola>{_ORA_ALT}|una)
    )
    (?:
        \s+in\s+punto
        |
        \s+e\s+(?:un\s+)?(?P<min_parola>quarto|mezzo|mezza|half|quarter|{_MIN_ALT}|\d{{1,2}})
        |
        \s+meno\s+(?:un\s+)?(?P<meno>quarto|quarter|{_MIN_ALT}|\d{{1,2}})
    )?
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ORA_E_MINUTI: Final[re.Pattern[str]] = re.compile(
    rf"""
    \b(?P<ora_nuda>{_ORA_ALT})
    \s+(?:
        in\s+punto
        |
        e\s+(?:un\s+)?(?P<min_nuda>quarto|mezzo|mezza|half|quarter|{_MIN_ALT}|\d{{1,2}})
        |
        meno\s+(?:un\s+)?(?P<meno_nuda>quarto|quarter|{_MIN_ALT}|\d{{1,2}})
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_GIORNO_CIFRA: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,2})[°ºo]?\s*$",
    re.IGNORECASE,
)
_ANNO_CIFRA: Final[re.Pattern[str]] = re.compile(r"\b(\d{4})\b")
_TOKEN: Final[re.Pattern[str]] = re.compile(r"[A-Za-zÀ-ÿ0-9]+")


def intero_da_parole(valore: object) -> int | None:
    """Cardinal written in Italian or English, 0–9999, or a digit string."""
    if isinstance(valore, int):
        return valore if 0 <= valore <= 9999 else None
    if not isinstance(valore, str):
        return None
    grezzo = valore.strip()
    if not grezzo:
        return None
    if re.fullmatch(r"\d{1,4}", grezzo):
        numero = int(grezzo)
        return numero if 0 <= numero <= 9999 else None
    blob = _compatta(grezzo)
    if blob in _MAPPA:
        return _MAPPA[blob]
    if blob in _ORDINALI:
        return _ORDINALI[blob]
    parti = [p for p in _TOKEN.findall(grezzo.casefold()) if p]
    while len(parti) > 1 and _compatta(parti[0]) in _ARTICOLI:
        parti = parti[1:]
    blob = _compatta("".join(parti))
    if blob in _MAPPA:
        return _MAPPA[blob]
    if blob in _ORDINALI:
        return _ORDINALI[blob]
    return None


def _minuto_da_pezzo(pezzo: str | None) -> int | None:
    if pezzo is None:
        return None
    testo = pezzo.strip()
    if re.fullmatch(r"\d{1,2}", testo):
        minuto = int(testo)
        return minuto if 0 <= minuto <= 59 else None
    return _MINUTI_FORME.get(_compatta(testo))


def _orario_da_match(
    ora: int,
    extra: str | None,
    meno: str | None,
    in_punto: bool,
    *,
    precisione_base: str,
) -> OrarioParole | None:
    if not 0 <= ora <= 23:
        return None
    minuto = 0
    precisione = "minuto" if in_punto else precisione_base
    if extra is not None:
        minuto_n = _minuto_da_pezzo(extra)
        if minuto_n is None:
            return None
        minuto = minuto_n
        precisione = "minuto"
    elif meno is not None:
        sottrai = _minuto_da_pezzo(meno)
        if sottrai is None:
            return None
        totale = ora * 60 - sottrai
        if totale < 0:
            totale += 24 * 60
        ora, minuto = divmod(totale, 60)
        precisione = "minuto"
    return OrarioParole(ora % 24, minuto, 0, precisione)


def orario_da_parole(valore: object) -> OrarioParole | None:
    """Clock of day from a spoken or mixed span. Does not invent a date."""
    if not isinstance(valore, str):
        return None
    testo = valore.strip()
    if not testo:
        return None
    lessicale = _OREFICE.get(_compatta(testo))
    if lessicale is not None:
        ora, minuto, secondo, precisione = lessicale
        return OrarioParole(ora, minuto, secondo, precisione)

    trovato = _ORA_PAROLE.search(testo)
    if trovato is not None:
        in_punto = bool(re.search(r"\bin\s+punto\b", trovato.group(0), re.IGNORECASE))
        extra = trovato.group("min_parola")
        meno = trovato.group("meno")
        if trovato.group("lessico"):
            ora, _, _, precisione = _OREFICE[_compatta(trovato.group("lessico"))]
            return _orario_da_match(
                ora, extra, meno, in_punto, precisione_base=precisione
            )
        parola = trovato.group("ora_parola")
        if parola:
            ora = _ORE_FORME.get(_compatta(parola))
            if ora is not None:
                return _orario_da_match(
                    ora, extra, meno, in_punto, precisione_base="ora"
                )

    nudo = _ORA_E_MINUTI.search(testo)
    if nudo is None:
        return None
    ora = _ORE_FORME.get(_compatta(nudo.group("ora_nuda") or ""))
    if ora is None:
        return None
    in_punto = bool(re.search(r"\bin\s+punto\b", nudo.group(0), re.IGNORECASE))
    return _orario_da_match(
        ora,
        nudo.group("min_nuda"),
        nudo.group("meno_nuda"),
        in_punto,
        precisione_base="ora",
    )


def _giorno_da_sinistra(sinistra: str) -> int | None:
    testo = sinistra.strip()
    if not testo:
        return None
    cifra = _GIORNO_CIFRA.search(testo)
    if cifra is not None:
        giorno = int(cifra.group(1))
        return giorno if 1 <= giorno <= 31 else None
    tokens = _TOKEN.findall(testo)
    for ampiezza in range(1, min(4, len(tokens) + 1)):
        pezzo = "".join(tokens[-ampiezza:])
        numero = intero_da_parole(pezzo)
        if numero is not None and 1 <= numero <= 31:
            return numero
        ordinale = _ORDINALI.get(_compatta(pezzo))
        if ordinale is not None:
            return ordinale
    return None


def _anno_da_destra(destra: str) -> int | None:
    testo = destra.strip()
    if not testo:
        return None
    cifra = _ANNO_CIFRA.search(testo)
    if cifra is not None:
        anno = int(cifra.group(1))
        return anno if 1 <= anno <= 9999 else None
    testa = re.split(
        r",|\b(?:ore|alle|all['’]|at)\b",
        testo,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    anno = intero_da_parole(testa)
    if anno is not None and 1 <= anno <= 9999:
        return anno
    return None


def data_da_parole(
    valore: object, mesi: dict[str, int]
) -> tuple[int | None, int, int] | None:
    """Day (optional), month, year from a written date that names the month."""
    if not isinstance(valore, str):
        return None
    testo = valore.strip()
    if not testo:
        return None
    trovato: re.Match[str] | None = None
    mese_n: int | None = None
    for nome, numero in sorted(mesi.items(), key=lambda item: len(item[0]), reverse=True):
        match = re.search(rf"\b{re.escape(nome)}\b", testo, re.IGNORECASE)
        if match is None:
            continue
        if trovato is None or match.start() < trovato.start():
            trovato = match
            mese_n = numero
    if trovato is None or mese_n is None:
        return None
    giorno = _giorno_da_sinistra(testo[: trovato.start()])
    anno = _anno_da_destra(testo[trovato.end() :])
    if anno is None:
        return None
    return giorno, mese_n, anno


__all__ = [
    "OrarioParole",
    "data_da_parole",
    "intero_da_parole",
    "orario_da_parole",
]
