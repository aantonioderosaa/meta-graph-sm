"""One-shot builder for event_graph_eval_corpus.json. Not imported by tests."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("event_graph_eval_corpus.json")


def ev(
    lemma,
    span,
    *,
    e_testa=True,
    tempo="passato",
    modalita="fattuale",
    polarita_negata=False,
    iterativo=False,
    fattualita="FATTUALE",
    marca_dialogo=False,
    sogg=None,
    sogg_tipo="nome_proprio",
    sogg_speciale="nessuno",
    extra_args=None,
):
    args = []
    if sogg is not None:
        args.append({"ruolo": "SOGG", "forma": sogg, "tipo_superficiale": sogg_tipo})
    if extra_args:
        args.extend(extra_args)
    return {
        "lemma": lemma,
        "span": span,
        "e_testa": e_testa,
        "tempo": tempo,
        "modalita": modalita,
        "polarita_negata": polarita_negata,
        "iterativo": iterativo,
        "fattualita": fattualita,
        "marca_dialogo": marca_dialogo,
        "argomenti": args,
        "sogg_speciale": sogg_speciale,
    }


def rel(da, a, tipo, allen=None, conf=1.0, intra=True):
    return {
        "da_indice": da,
        "a_indice": a,
        "tipo": tipo,
        "relazione_allen": allen,
        "confidenza": conf,
        "intra_frase": intra,
    }


def ogg(forma, tipo="sn_comune"):
    return {"ruolo": "OGG", "forma": forma, "tipo_superficiale": tipo}


RAW: list[dict] = []


def add(eid, lang, text, phenomena, events, rels=None, sentences=None, tipi=None):
    RAW.append(
        {
            "id": eid,
            "lang": lang,
            "text": text,
            "phenomena": phenomena,
            "events": events,
            "rels": rels or [],
            "sentences": sentences,
            "tipi": tipi,
        }
    )


# --- Italian -----------------------------------------------------------------

add(
    "eval-001",
    "it",
    "Marco arrivò, si sedette e accese la lampada.",
    ["coordinated_predicates"],
    [
        ev("arrivare", "arrivò", sogg="Marco"),
        ev("sedersi", "si sedette", sogg="Marco"),
        ev(
            "accendere",
            "accese la lampada",
            sogg="Marco",
            extra_args=[ogg("la lampada")],
        ),
    ],
    [rel(0, 1, "SEQUENZA", "meets"), rel(1, 2, "SEQUENZA", "meets")],
)
add(
    "eval-002",
    "it",
    "Anna aprì la finestra, guardò fuori e sorrise.",
    ["coordinated_predicates"],
    [
        ev("aprire", "aprì la finestra", sogg="Anna", extra_args=[ogg("la finestra")]),
        ev("guardare", "guardò fuori", sogg="Anna"),
        ev("sorridere", "sorrise", sogg="Anna"),
    ],
    [rel(0, 1, "SEQUENZA", "meets"), rel(1, 2, "SEQUENZA", "meets")],
)
add(
    "eval-003",
    "it",
    "Luca corse, cadde, si rialzò e ripartì.",
    ["coordinated_predicates", "asyndeton"],
    [
        ev("correre", "corse", sogg="Luca"),
        ev("cadere", "cadde", sogg="Luca"),
        ev("rialzarsi", "si rialzò", sogg="Luca"),
        ev("ripartire", "ripartì", sogg="Luca"),
    ],
    [
        rel(0, 1, "SEQUENZA", "meets"),
        rel(1, 2, "SEQUENZA", "meets"),
        rel(2, 3, "SEQUENZA", "meets"),
    ],
)
add(
    "eval-004",
    "it",
    "Marco disse: «Torno subito.»",
    ["direct_speech"],
    [
        ev(
            "dire",
            "Marco disse: «Torno subito.»",
            marca_dialogo=True,
            sogg="Marco",
        )
    ],
)
add(
    "eval-005",
    "it",
    "La madre chiese: «Hai mangiato?»",
    ["direct_speech"],
    [
        ev(
            "chiedere",
            "La madre chiese: «Hai mangiato?»",
            marca_dialogo=True,
            sogg="La madre",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-006",
    "it",
    "«Non partire», sussurrò Elena.",
    ["direct_speech"],
    [
        ev(
            "sussurrare",
            "«Non partire», sussurrò Elena.",
            marca_dialogo=True,
            sogg="Elena",
        )
    ],
)
add(
    "eval-007",
    "it",
    "Marco indossava un mantello scuro.",
    ["state", "imperfect"],
    [
        ev(
            "indossare",
            "indossava un mantello scuro",
            tempo="imperfetto",
            sogg="Marco",
            extra_args=[ogg("un mantello scuro")],
        )
    ],
)
add(
    "eval-008",
    "it",
    "La porta era chiusa.",
    ["state", "imperfect"],
    [
        ev(
            "essere",
            "era chiusa",
            tempo="imperfetto",
            sogg="La porta",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-009",
    "it",
    "Il cielo era grigio e il vento soffiava.",
    ["state", "imperfect", "coordinated_predicates"],
    [
        ev(
            "essere",
            "era grigio",
            tempo="imperfetto",
            sogg="Il cielo",
            sogg_tipo="sn_comune",
        ),
        ev(
            "soffiare",
            "soffiava",
            tempo="imperfetto",
            sogg="il vento",
            sogg_tipo="sn_comune",
        ),
    ],
    [rel(0, 1, "COLLEGATO", "overlaps")],
)
add(
    "eval-010",
    "it",
    "Marco poteva partire all'alba.",
    ["modal"],
    [
        ev(
            "partire",
            "poteva partire all'alba",
            tempo="imperfetto",
            modalita="ipotetico",
            fattualita="NON_FATTUALE",
            sogg="Marco",
        )
    ],
)
add(
    "eval-011",
    "it",
    "Anna voleva vincere la gara.",
    ["modal"],
    [
        ev(
            "vincere",
            "voleva vincere la gara",
            tempo="imperfetto",
            modalita="volitivo",
            fattualita="NON_FATTUALE",
            sogg="Anna",
            extra_args=[ogg("la gara")],
        )
    ],
)
add(
    "eval-012",
    "it",
    "Luca doveva consegnare il rapporto.",
    ["modal"],
    [
        ev(
            "consegnare",
            "doveva consegnare il rapporto",
            tempo="imperfetto",
            modalita="deontico",
            fattualita="NON_FATTUALE",
            sogg="Luca",
            extra_args=[ogg("il rapporto")],
        )
    ],
)
add(
    "eval-013",
    "it",
    "Se Marco fosse arrivato in tempo, avrebbe visto lo spettacolo.",
    ["condition", "modal"],
    [
        ev(
            "arrivare",
            "fosse arrivato in tempo",
            e_testa=False,
            tempo="trapassato",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="Marco",
        ),
        ev(
            "vedere",
            "avrebbe visto lo spettacolo",
            tempo="passato",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="Marco",
            extra_args=[ogg("lo spettacolo")],
        ),
    ],
    [rel(0, 1, "CONDIZIONE")],
)
add(
    "eval-014",
    "it",
    "Se piovesse, resterei a casa.",
    ["condition", "modal"],
    [
        ev(
            "piovere",
            "piovesse",
            e_testa=False,
            tempo="imperfetto",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg_speciale="NON_APPLICABILE",
        ),
        ev(
            "restare",
            "resterei a casa",
            tempo="presente",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg_speciale="IGNOTO",
            extra_args=[{"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"}],
        ),
    ],
    [rel(0, 1, "CONDIZIONE")],
)
add(
    "eval-015",
    "it",
    "Marco restò a casa perché pioveva.",
    ["cause", "imperfect"],
    [
        ev("restare", "restò a casa", sogg="Marco"),
        ev(
            "piovere",
            "pioveva",
            e_testa=False,
            tempo="imperfetto",
            sogg_speciale="NON_APPLICABILE",
        ),
    ],
    [rel(1, 0, "CAUSA")],
)
add(
    "eval-016",
    "it",
    "Anna sorrise perché aveva vinto.",
    ["cause", "pluperfect"],
    [
        ev("sorridere", "sorrise", sogg="Anna"),
        ev(
            "vincere",
            "aveva vinto",
            e_testa=False,
            tempo="trapassato",
            sogg="Anna",
        ),
    ],
    [rel(1, 0, "CAUSA", "before")],
)
add(
    "eval-017",
    "it",
    "Pioveva, quindi Marco restò a casa.",
    ["cause", "imperfect"],
    [
        ev(
            "piovere",
            "Pioveva",
            tempo="imperfetto",
            sogg_speciale="NON_APPLICABILE",
        ),
        ev("restare", "restò a casa", sogg="Marco"),
    ],
    [rel(0, 1, "CAUSA")],
)
add(
    "eval-018",
    "it",
    "Il treno era in ritardo. Quindi Marco perse l'appuntamento.",
    ["cause", "state"],
    [
        ev(
            "essere",
            "era in ritardo",
            tempo="imperfetto",
            sogg="Il treno",
            sogg_tipo="sn_comune",
        ),
        ev(
            "perdere",
            "perse l'appuntamento",
            sogg="Marco",
            extra_args=[ogg("l'appuntamento")],
        ),
    ],
    [rel(0, 1, "CAUSA", intra=False)],
    sentences=["Il treno era in ritardo.", "Quindi Marco perse l'appuntamento."],
)
add(
    "eval-019",
    "it",
    "Marco leggeva mentre Anna cucinava.",
    ["mentre", "imperfect", "state"],
    [
        ev("leggere", "leggeva", tempo="imperfetto", sogg="Marco"),
        ev(
            "cucinare",
            "cucinava",
            e_testa=False,
            tempo="imperfetto",
            sogg="Anna",
        ),
    ],
    [rel(0, 1, "COLLEGATO", "overlaps")],
)
add(
    "eval-020",
    "it",
    "Marco prometteva aiuto, mentre non muoveva un dito.",
    ["mentre", "contrast", "imperfect"],
    [
        ev(
            "promettere",
            "prometteva aiuto",
            tempo="imperfetto",
            sogg="Marco",
            extra_args=[ogg("aiuto")],
        ),
        ev(
            "muovere",
            "non muoveva un dito",
            e_testa=False,
            tempo="imperfetto",
            polarita_negata=True,
            sogg="Marco",
            extra_args=[ogg("un dito")],
        ),
    ],
    [rel(0, 1, "COLLEGATO")],
)
add(
    "eval-021",
    "it",
    "Marco disse di partire all'alba.",
    ["completive"],
    [
        ev("dire", "disse", sogg="Marco"),
        ev(
            "partire",
            "partire all'alba",
            e_testa=False,
            tempo="non_finito",
            fattualita="NON_FATTUALE",
            sogg="Marco",
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-022",
    "it",
    "Anna pensava che Luca avesse mentito.",
    ["completive", "pluperfect"],
    [
        ev("pensare", "pensava", tempo="imperfetto", sogg="Anna"),
        ev(
            "mentire",
            "avesse mentito",
            e_testa=False,
            tempo="trapassato",
            modalita="ipotetico",
            fattualita="NON_FATTUALE",
            sogg="Luca",
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-023",
    "it",
    "Il capitano ordinò di salpare.",
    ["completive"],
    [
        ev(
            "ordinare",
            "ordinò",
            sogg="Il capitano",
            sogg_tipo="sn_comune",
        ),
        ev(
            "salpare",
            "salpare",
            e_testa=False,
            tempo="non_finito",
            modalita="deontico",
            fattualita="NON_FATTUALE",
            sogg_speciale="IGNOTO",
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-024",
    "it",
    "Marco arrivò. Aveva perso il treno.",
    ["pluperfect"],
    [
        ev("arrivare", "arrivò", sogg="Marco"),
        ev(
            "perdere",
            "Aveva perso il treno",
            tempo="trapassato",
            sogg="Marco",
            extra_args=[ogg("il treno")],
        ),
    ],
    [rel(1, 0, "PRECEDE", "before", intra=False)],
    sentences=["Marco arrivò.", "Aveva perso il treno."],
)
add(
    "eval-025",
    "it",
    "Quando Marco arrivò, Anna era già partita.",
    ["pluperfect"],
    [
        ev("arrivare", "arrivò", e_testa=False, sogg="Marco"),
        ev(
            "partire",
            "era già partita",
            tempo="trapassato",
            sogg="Anna",
        ),
    ],
    [rel(1, 0, "PRECEDE", "before")],
)
add(
    "eval-026",
    "it",
    "Luca sorrise. Aveva capito tutto.",
    ["pluperfect"],
    [
        ev("sorridere", "sorrise", sogg="Luca"),
        ev("capire", "Aveva capito tutto", tempo="trapassato", sogg="Luca"),
    ],
    [rel(1, 0, "PRECEDE", "before", intra=False)],
    sentences=["Luca sorrise.", "Aveva capito tutto."],
)
add(
    "eval-027",
    "it",
    "Entrò. Si fermò. Guardò.",
    ["asyndeton", "orphan"],
    [
        ev(
            "entrare",
            "Entrò",
            sogg_speciale="IGNOTO",
            extra_args=[{"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"}],
        ),
        ev(
            "fermarsi",
            "Si fermò",
            sogg_speciale="IGNOTO",
            extra_args=[{"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"}],
        ),
        ev(
            "guardare",
            "Guardò",
            sogg_speciale="IGNOTO",
            extra_args=[{"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"}],
        ),
    ],
    [
        rel(0, 1, "SEQUENZA", "meets", intra=False),
        rel(1, 2, "SEQUENZA", "meets", intra=False),
    ],
    sentences=["Entrò.", "Si fermò.", "Guardò."],
)
add(
    "eval-028",
    "it",
    "Anna accese la luce, spense la radio, chiuse la porta.",
    ["asyndeton", "orphan", "coordinated_predicates"],
    [
        ev("accendere", "accese la luce", sogg="Anna", extra_args=[ogg("la luce")]),
        ev("spegnere", "spense la radio", sogg="Anna", extra_args=[ogg("la radio")]),
        ev("chiudere", "chiuse la porta", sogg="Anna", extra_args=[ogg("la porta")]),
    ],
    [rel(0, 1, "SEQUENZA", "meets"), rel(1, 2, "SEQUENZA", "meets")],
)
add(
    "eval-029",
    "it",
    "Il ritardo provocò la cancellazione del volo.",
    ["nested", "cause"],
    [
        ev(
            "provocare",
            "provocò",
            sogg="Il ritardo",
            sogg_tipo="sn_comune",
        ),
        ev(
            "cancellare",
            "cancellazione del volo",
            e_testa=False,
            tempo="non_finito",
            sogg_speciale="NON_APPLICABILE",
        ),
    ],
    [rel(0, 1, "CAUSA")],
)
add(
    "eval-030",
    "it",
    "Marco iniziò a scrivere la lettera.",
    ["nested", "completive"],
    [
        ev("iniziare", "iniziò", sogg="Marco"),
        ev(
            "scrivere",
            "scrivere la lettera",
            e_testa=False,
            tempo="non_finito",
            sogg="Marco",
            extra_args=[ogg("la lettera")],
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-031",
    "it",
    "La notizia fece scappare tutti.",
    ["nested", "cause"],
    [
        ev(
            "fare",
            "fece scappare",
            sogg="La notizia",
            sogg_tipo="sn_comune",
        ),
        ev(
            "scappare",
            "scappare tutti",
            e_testa=False,
            tempo="non_finito",
            sogg="tutti",
            sogg_tipo="sn_comune",
        ),
    ],
    [rel(0, 1, "CAUSA")],
)
add(
    "eval-032",
    "it",
    "Marco camminava lungo il fiume.",
    ["imperfect", "state"],
    [ev("camminare", "camminava lungo il fiume", tempo="imperfetto", sogg="Marco")],
)
add(
    "eval-033",
    "it",
    "Marco leggeva quando squillò il telefono.",
    ["imperfect"],
    [
        ev("leggere", "leggeva", tempo="imperfetto", sogg="Marco"),
        ev(
            "squillare",
            "squillò il telefono",
            e_testa=False,
            sogg="il telefono",
            sogg_tipo="sn_comune",
        ),
    ],
    [rel(1, 0, "COLLEGATO", "during")],
)
add(
    "eval-034",
    "it",
    "Il Dr. Rossi arrivò alle tre.",
    ["abbreviation"],
    [ev("arrivare", "arrivò alle tre", sogg="Il Dr. Rossi", sogg_tipo="nome_proprio")],
)
add(
    "eval-035",
    "it",
    "La Sig.ra Bianchi firmò il contratto il 12 gen. 2020.",
    ["abbreviation"],
    [
        ev(
            "firmare",
            "firmò il contratto",
            sogg="La Sig.ra Bianchi",
            extra_args=[ogg("il contratto")],
        )
    ],
)
add(
    "eval-036",
    "it",
    "Il Prof. Neri citò p. 12 del volume.",
    ["abbreviation"],
    [
        ev(
            "citare",
            "citò p. 12 del volume",
            sogg="Il Prof. Neri",
        )
    ],
)
add(
    "eval-037",
    "it",
    "La temperatura salì a 36.5 gradi.",
    ["decimal"],
    [
        ev(
            "salire",
            "salì a 36.5 gradi",
            sogg="La temperatura",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-038",
    "it",
    "Il treno delle 15.30 partì in orario.",
    ["decimal"],
    [
        ev(
            "partire",
            "partì in orario",
            sogg="Il treno delle 15.30",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-039",
    "it",
    "Marco è già partito?",
    ["interrogative"],
    [
        ev(
            "partire",
            "è già partito",
            tempo="passato",
            fattualita="NON_FATTUALE",
            sogg="Marco",
        )
    ],
)
add(
    "eval-040",
    "it",
    "Chi ha chiuso la porta?",
    ["interrogative"],
    [
        ev(
            "chiudere",
            "ha chiuso la porta",
            fattualita="NON_FATTUALE",
            sogg="Chi",
            sogg_tipo="pronome",
            extra_args=[ogg("la porta")],
        )
    ],
)
add(
    "eval-041",
    "it",
    "Chiudi la porta.",
    ["imperative"],
    [
        ev(
            "chiudere",
            "Chiudi la porta",
            tempo="presente",
            fattualita="NON_FATTUALE",
            sogg_speciale="NON_APPLICABILE",
            extra_args=[
                {"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"},
                ogg("la porta"),
            ],
        )
    ],
)
add(
    "eval-042",
    "it",
    "Non toccare il forno.",
    ["imperative"],
    [
        ev(
            "toccare",
            "Non toccare il forno",
            tempo="non_finito",
            polarita_negata=True,
            fattualita="NON_FATTUALE",
            sogg_speciale="NON_APPLICABILE",
            extra_args=[
                {"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"},
                ogg("il forno"),
            ],
        )
    ],
)
add(
    "eval-043",
    "it",
    "Marco partirà domani.",
    ["future"],
    [
        ev(
            "partire",
            "partirà domani",
            tempo="futuro",
            fattualita="NON_FATTUALE",
            sogg="Marco",
        )
    ],
)
add(
    "eval-044",
    "it",
    "Anna arriverà alle otto e porterà i documenti.",
    ["future", "coordinated_predicates"],
    [
        ev(
            "arrivare",
            "arriverà alle otto",
            tempo="futuro",
            fattualita="NON_FATTUALE",
            sogg="Anna",
        ),
        ev(
            "portare",
            "porterà i documenti",
            tempo="futuro",
            fattualita="NON_FATTUALE",
            sogg="Anna",
            extra_args=[ogg("i documenti")],
        ),
    ],
    [rel(0, 1, "SEQUENZA", "before")],
)
add(
    "eval-045",
    "it",
    "Sebbene piovesse, Marco uscì.",
    ["contrast", "imperfect"],
    [
        ev(
            "piovere",
            "piovesse",
            e_testa=False,
            tempo="imperfetto",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg_speciale="NON_APPLICABILE",
        ),
        ev("uscire", "uscì", sogg="Marco"),
    ],
    [rel(0, 1, "CONCESSIONE")],
)
add(
    "eval-046",
    "it",
    "Marco uscì per comprare il pane.",
    ["nested"],
    [
        ev("uscire", "uscì", sogg="Marco"),
        ev(
            "comprare",
            "comprare il pane",
            e_testa=False,
            tempo="non_finito",
            fattualita="NON_FATTUALE",
            sogg="Marco",
            extra_args=[ogg("il pane")],
        ),
    ],
    [rel(1, 0, "SCOPO")],
)
add(
    "eval-047",
    "it",
    "Marco non arrivò in tempo.",
    ["state"],
    [
        ev(
            "arrivare",
            "non arrivò in tempo",
            polarita_negata=True,
            sogg="Marco",
        )
    ],
)
add(
    "eval-048",
    "it",
    "Marco bussava ogni mattina.",
    ["imperfect"],
    [
        ev(
            "bussare",
            "bussava ogni mattina",
            tempo="imperfetto",
            iterativo=True,
            sogg="Marco",
        )
    ],
)
add(
    "eval-049",
    "it",
    "Marco era stanco, ma continuò a camminare.",
    ["contrast", "state", "imperfect"],
    [
        ev("essere", "era stanco", tempo="imperfetto", sogg="Marco"),
        ev("continuare", "continuò a camminare", sogg="Marco"),
    ],
    [rel(0, 1, "CONTRASTO")],
)
add(
    "eval-050",
    "it",
    "Con più tempo, Marco avrebbe finito il lavoro.",
    ["modal", "condition"],
    [
        ev(
            "finire",
            "avrebbe finito il lavoro",
            tempo="passato",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="Marco",
            extra_args=[ogg("il lavoro")],
        )
    ],
)

# --- English -----------------------------------------------------------------

add(
    "eval-051",
    "en",
    "Mark arrived, sat down and lit the lamp.",
    ["coordinated_predicates"],
    [
        ev("arrive", "arrived", sogg="Mark"),
        ev("sit", "sat down", sogg="Mark"),
        ev("light", "lit the lamp", sogg="Mark", extra_args=[ogg("the lamp")]),
    ],
    [rel(0, 1, "SEQUENZA", "meets"), rel(1, 2, "SEQUENZA", "meets")],
)
add(
    "eval-052",
    "en",
    "Anna opened the window, looked out and smiled.",
    ["coordinated_predicates"],
    [
        ev("open", "opened the window", sogg="Anna", extra_args=[ogg("the window")]),
        ev("look", "looked out", sogg="Anna"),
        ev("smile", "smiled", sogg="Anna"),
    ],
    [rel(0, 1, "SEQUENZA", "meets"), rel(1, 2, "SEQUENZA", "meets")],
)
add(
    "eval-053",
    "en",
    "Luke ran, fell, got up and started again.",
    ["coordinated_predicates", "asyndeton"],
    [
        ev("run", "ran", sogg="Luke"),
        ev("fall", "fell", sogg="Luke"),
        ev("get_up", "got up", sogg="Luke"),
        ev("start", "started again", sogg="Luke"),
    ],
    [
        rel(0, 1, "SEQUENZA", "meets"),
        rel(1, 2, "SEQUENZA", "meets"),
        rel(2, 3, "SEQUENZA", "meets"),
    ],
)
add(
    "eval-054",
    "en",
    'Mark said: "I\'ll be right back."',
    ["direct_speech"],
    [
        ev(
            "say",
            'Mark said: "I\'ll be right back."',
            marca_dialogo=True,
            sogg="Mark",
        )
    ],
)
add(
    "eval-055",
    "en",
    'Mother asked: "Have you eaten?"',
    ["direct_speech"],
    [
        ev(
            "ask",
            'Mother asked: "Have you eaten?"',
            marca_dialogo=True,
            sogg="Mother",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-056",
    "en",
    '"Don\'t leave," Elena whispered.',
    ["direct_speech"],
    [
        ev(
            "whisper",
            '"Don\'t leave," Elena whispered.',
            marca_dialogo=True,
            sogg="Elena",
        )
    ],
)
add(
    "eval-057",
    "en",
    "Mark was wearing a dark cloak.",
    ["state", "imperfect"],
    [
        ev(
            "wear",
            "was wearing a dark cloak",
            tempo="imperfetto",
            sogg="Mark",
            extra_args=[ogg("a dark cloak")],
        )
    ],
)
add(
    "eval-058",
    "en",
    "The door was closed.",
    ["state", "imperfect"],
    [
        ev(
            "be",
            "was closed",
            tempo="imperfetto",
            sogg="The door",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-059",
    "en",
    "The sky was grey and the wind was blowing.",
    ["state", "imperfect", "coordinated_predicates"],
    [
        ev(
            "be",
            "was grey",
            tempo="imperfetto",
            sogg="The sky",
            sogg_tipo="sn_comune",
        ),
        ev(
            "blow",
            "was blowing",
            tempo="imperfetto",
            sogg="the wind",
            sogg_tipo="sn_comune",
        ),
    ],
    [rel(0, 1, "COLLEGATO", "overlaps")],
)
add(
    "eval-060",
    "en",
    "Mark could leave at dawn.",
    ["modal"],
    [
        ev(
            "leave",
            "could leave at dawn",
            tempo="passato",
            modalita="ipotetico",
            fattualita="NON_FATTUALE",
            sogg="Mark",
        )
    ],
)
add(
    "eval-061",
    "en",
    "Anna wanted to win the race.",
    ["modal"],
    [
        ev(
            "win",
            "wanted to win the race",
            tempo="passato",
            modalita="volitivo",
            fattualita="NON_FATTUALE",
            sogg="Anna",
            extra_args=[ogg("the race")],
        )
    ],
)
add(
    "eval-062",
    "en",
    "Luke had to deliver the report.",
    ["modal"],
    [
        ev(
            "deliver",
            "had to deliver the report",
            tempo="passato",
            modalita="deontico",
            fattualita="NON_FATTUALE",
            sogg="Luke",
            extra_args=[ogg("the report")],
        )
    ],
)
add(
    "eval-063",
    "en",
    "If Mark had arrived on time, he would have seen the show.",
    ["condition", "modal", "pluperfect"],
    [
        ev(
            "arrive",
            "had arrived on time",
            e_testa=False,
            tempo="trapassato",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="Mark",
        ),
        ev(
            "see",
            "would have seen the show",
            tempo="passato",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="he",
            sogg_tipo="pronome",
            extra_args=[ogg("the show")],
        ),
    ],
    [rel(0, 1, "CONDIZIONE")],
)
add(
    "eval-064",
    "en",
    "If it rained, I would stay home.",
    ["condition", "modal"],
    [
        ev(
            "rain",
            "rained",
            e_testa=False,
            tempo="passato",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="it",
            sogg_tipo="pronome",
            sogg_speciale="NON_APPLICABILE",
        ),
        ev(
            "stay",
            "would stay home",
            tempo="presente",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="I",
            sogg_tipo="pronome",
        ),
    ],
    [rel(0, 1, "CONDIZIONE")],
)
add(
    "eval-065",
    "en",
    "Mark stayed home because it was raining.",
    ["cause", "imperfect"],
    [
        ev("stay", "stayed home", sogg="Mark"),
        ev(
            "rain",
            "was raining",
            e_testa=False,
            tempo="imperfetto",
            sogg="it",
            sogg_tipo="pronome",
            sogg_speciale="NON_APPLICABILE",
        ),
    ],
    [rel(1, 0, "CAUSA")],
)
add(
    "eval-066",
    "en",
    "Anna smiled because she had won.",
    ["cause", "pluperfect"],
    [
        ev("smile", "smiled", sogg="Anna"),
        ev(
            "win",
            "had won",
            e_testa=False,
            tempo="trapassato",
            sogg="she",
            sogg_tipo="pronome",
        ),
    ],
    [rel(1, 0, "CAUSA", "before")],
)
add(
    "eval-067",
    "en",
    "It was raining, so Mark stayed home.",
    ["cause", "imperfect"],
    [
        ev(
            "rain",
            "was raining",
            tempo="imperfetto",
            sogg="It",
            sogg_tipo="pronome",
            sogg_speciale="NON_APPLICABILE",
        ),
        ev("stay", "stayed home", sogg="Mark"),
    ],
    [rel(0, 1, "CAUSA")],
)
add(
    "eval-068",
    "en",
    "The train was late. Therefore Mark missed the appointment.",
    ["cause", "state"],
    [
        ev(
            "be",
            "was late",
            tempo="imperfetto",
            sogg="The train",
            sogg_tipo="sn_comune",
        ),
        ev(
            "miss",
            "missed the appointment",
            sogg="Mark",
            extra_args=[ogg("the appointment")],
        ),
    ],
    [rel(0, 1, "CAUSA", intra=False)],
    sentences=["The train was late.", "Therefore Mark missed the appointment."],
)
add(
    "eval-069",
    "en",
    "Mark was reading while Anna was cooking.",
    ["mentre", "imperfect", "state"],
    [
        ev("read", "was reading", tempo="imperfetto", sogg="Mark"),
        ev(
            "cook",
            "was cooking",
            e_testa=False,
            tempo="imperfetto",
            sogg="Anna",
        ),
    ],
    [rel(0, 1, "COLLEGATO", "overlaps")],
)
add(
    "eval-070",
    "en",
    "Mark promised to help, while he did not lift a finger.",
    ["mentre", "contrast"],
    [
        ev("promise", "promised to help", sogg="Mark"),
        ev(
            "lift",
            "did not lift a finger",
            e_testa=False,
            polarita_negata=True,
            sogg="he",
            sogg_tipo="pronome",
            extra_args=[ogg("a finger")],
        ),
    ],
    [rel(0, 1, "COLLEGATO")],
)
add(
    "eval-071",
    "en",
    "Mark said that he would leave at dawn.",
    ["completive"],
    [
        ev("say", "said", sogg="Mark"),
        ev(
            "leave",
            "would leave at dawn",
            e_testa=False,
            tempo="futuro",
            fattualita="NON_FATTUALE",
            sogg="he",
            sogg_tipo="pronome",
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-072",
    "en",
    "Anna thought that Luke had lied.",
    ["completive", "pluperfect"],
    [
        ev("think", "thought", sogg="Anna"),
        ev(
            "lie",
            "had lied",
            e_testa=False,
            tempo="trapassato",
            modalita="ipotetico",
            fattualita="NON_FATTUALE",
            sogg="Luke",
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-073",
    "en",
    "The captain ordered them to set sail.",
    ["completive"],
    [
        ev(
            "order",
            "ordered",
            sogg="The captain",
            sogg_tipo="sn_comune",
        ),
        ev(
            "sail",
            "set sail",
            e_testa=False,
            tempo="non_finito",
            modalita="deontico",
            fattualita="NON_FATTUALE",
            sogg="them",
            sogg_tipo="pronome",
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-074",
    "en",
    "Mark arrived. He had missed the train.",
    ["pluperfect"],
    [
        ev("arrive", "arrived", sogg="Mark"),
        ev(
            "miss",
            "had missed the train",
            tempo="trapassato",
            sogg="He",
            sogg_tipo="pronome",
            extra_args=[ogg("the train")],
        ),
    ],
    [rel(1, 0, "PRECEDE", "before", intra=False)],
    sentences=["Mark arrived.", "He had missed the train."],
)
add(
    "eval-075",
    "en",
    "When Mark arrived, Anna had already left.",
    ["pluperfect"],
    [
        ev("arrive", "arrived", e_testa=False, sogg="Mark"),
        ev(
            "leave",
            "had already left",
            tempo="trapassato",
            sogg="Anna",
        ),
    ],
    [rel(1, 0, "PRECEDE", "before")],
)
add(
    "eval-076",
    "en",
    "Luke smiled. He had understood everything.",
    ["pluperfect"],
    [
        ev("smile", "smiled", sogg="Luke"),
        ev(
            "understand",
            "had understood everything",
            tempo="trapassato",
            sogg="He",
            sogg_tipo="pronome",
        ),
    ],
    [rel(1, 0, "PRECEDE", "before", intra=False)],
    sentences=["Luke smiled.", "He had understood everything."],
)
add(
    "eval-077",
    "en",
    "He came in. He stopped. He looked.",
    ["asyndeton", "orphan"],
    [
        ev("come", "came in", sogg="He", sogg_tipo="pronome"),
        ev("stop", "stopped", sogg="He", sogg_tipo="pronome"),
        ev("look", "looked", sogg="He", sogg_tipo="pronome"),
    ],
    [
        rel(0, 1, "SEQUENZA", "meets", intra=False),
        rel(1, 2, "SEQUENZA", "meets", intra=False),
    ],
    sentences=["He came in.", "He stopped.", "He looked."],
)
add(
    "eval-078",
    "en",
    "Anna turned on the light, turned off the radio, closed the door.",
    ["asyndeton", "orphan", "coordinated_predicates"],
    [
        ev(
            "turn_on",
            "turned on the light",
            sogg="Anna",
            extra_args=[ogg("the light")],
        ),
        ev(
            "turn_off",
            "turned off the radio",
            sogg="Anna",
            extra_args=[ogg("the radio")],
        ),
        ev(
            "close",
            "closed the door",
            sogg="Anna",
            extra_args=[ogg("the door")],
        ),
    ],
    [rel(0, 1, "SEQUENZA", "meets"), rel(1, 2, "SEQUENZA", "meets")],
)
add(
    "eval-079",
    "en",
    "The delay caused the cancellation of the flight.",
    ["nested", "cause"],
    [
        ev(
            "cause",
            "caused",
            sogg="The delay",
            sogg_tipo="sn_comune",
        ),
        ev(
            "cancel",
            "cancellation of the flight",
            e_testa=False,
            tempo="non_finito",
            sogg_speciale="NON_APPLICABILE",
        ),
    ],
    [rel(0, 1, "CAUSA")],
)
add(
    "eval-080",
    "en",
    "Mark began to write the letter.",
    ["nested", "completive"],
    [
        ev("begin", "began", sogg="Mark"),
        ev(
            "write",
            "write the letter",
            e_testa=False,
            tempo="non_finito",
            sogg="Mark",
            extra_args=[ogg("the letter")],
        ),
    ],
    [rel(0, 1, "CONTENUTO")],
)
add(
    "eval-081",
    "en",
    "The news made everyone run away.",
    ["nested", "cause"],
    [
        ev(
            "make",
            "made everyone run away",
            sogg="The news",
            sogg_tipo="sn_comune",
        ),
        ev(
            "run",
            "run away",
            e_testa=False,
            tempo="non_finito",
            sogg="everyone",
            sogg_tipo="sn_comune",
        ),
    ],
    [rel(0, 1, "CAUSA")],
)
add(
    "eval-082",
    "en",
    "Mark was walking along the river.",
    ["imperfect", "state"],
    [
        ev(
            "walk",
            "was walking along the river",
            tempo="imperfetto",
            sogg="Mark",
        )
    ],
)
add(
    "eval-083",
    "en",
    "Mark was reading when the phone rang.",
    ["imperfect"],
    [
        ev("read", "was reading", tempo="imperfetto", sogg="Mark"),
        ev(
            "ring",
            "the phone rang",
            e_testa=False,
            sogg="the phone",
            sogg_tipo="sn_comune",
        ),
    ],
    [rel(1, 0, "COLLEGATO", "during")],
)
add(
    "eval-084",
    "en",
    "Dr. Rossi arrived at three.",
    ["abbreviation"],
    [ev("arrive", "arrived at three", sogg="Dr. Rossi")],
)
add(
    "eval-085",
    "en",
    "Mrs. Bianchi signed the contract on Jan. 12, 2020.",
    ["abbreviation"],
    [
        ev(
            "sign",
            "signed the contract",
            sogg="Mrs. Bianchi",
            extra_args=[ogg("the contract")],
        )
    ],
)
add(
    "eval-086",
    "en",
    "Prof. Neri cited p. 12 of the volume.",
    ["abbreviation"],
    [ev("cite", "cited p. 12 of the volume", sogg="Prof. Neri")],
)
add(
    "eval-087",
    "en",
    "The temperature rose to 36.5 degrees.",
    ["decimal"],
    [
        ev(
            "rise",
            "rose to 36.5 degrees",
            sogg="The temperature",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-088",
    "en",
    "The 15.30 train left on time.",
    ["decimal"],
    [
        ev(
            "leave",
            "left on time",
            sogg="The 15.30 train",
            sogg_tipo="sn_comune",
        )
    ],
)
add(
    "eval-089",
    "en",
    "Has Mark already left?",
    ["interrogative"],
    [
        ev(
            "leave",
            "Has Mark already left",
            tempo="passato",
            fattualita="NON_FATTUALE",
            sogg="Mark",
        )
    ],
)
add(
    "eval-090",
    "en",
    "Who closed the door?",
    ["interrogative"],
    [
        ev(
            "close",
            "closed the door",
            fattualita="NON_FATTUALE",
            sogg="Who",
            sogg_tipo="pronome",
            extra_args=[ogg("the door")],
        )
    ],
)
add(
    "eval-091",
    "en",
    "Close the door.",
    ["imperative"],
    [
        ev(
            "close",
            "Close the door",
            tempo="presente",
            fattualita="NON_FATTUALE",
            sogg_speciale="NON_APPLICABILE",
            extra_args=[
                {"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"},
                ogg("the door"),
            ],
        )
    ],
)
add(
    "eval-092",
    "en",
    "Do not touch the oven.",
    ["imperative"],
    [
        ev(
            "touch",
            "Do not touch the oven",
            tempo="presente",
            polarita_negata=True,
            fattualita="NON_FATTUALE",
            sogg_speciale="NON_APPLICABILE",
            extra_args=[
                {"ruolo": "SOGG", "forma": "", "tipo_superficiale": "sogg_nullo"},
                ogg("the oven"),
            ],
        )
    ],
)
add(
    "eval-093",
    "en",
    "Mark will leave tomorrow.",
    ["future"],
    [
        ev(
            "leave",
            "will leave tomorrow",
            tempo="futuro",
            fattualita="NON_FATTUALE",
            sogg="Mark",
        )
    ],
)
add(
    "eval-094",
    "en",
    "Anna will arrive at eight and will bring the documents.",
    ["future", "coordinated_predicates"],
    [
        ev(
            "arrive",
            "will arrive at eight",
            tempo="futuro",
            fattualita="NON_FATTUALE",
            sogg="Anna",
        ),
        ev(
            "bring",
            "will bring the documents",
            tempo="futuro",
            fattualita="NON_FATTUALE",
            sogg="Anna",
            extra_args=[ogg("the documents")],
        ),
    ],
    [rel(0, 1, "SEQUENZA", "before")],
)
add(
    "eval-095",
    "en",
    "Although it was raining, Mark went out.",
    ["contrast", "imperfect"],
    [
        ev(
            "rain",
            "was raining",
            e_testa=False,
            tempo="imperfetto",
            sogg="it",
            sogg_tipo="pronome",
            sogg_speciale="NON_APPLICABILE",
        ),
        ev("go_out", "went out", sogg="Mark"),
    ],
    [rel(0, 1, "CONCESSIONE")],
)
add(
    "eval-096",
    "en",
    "Mark went out to buy bread.",
    ["nested"],
    [
        ev("go_out", "went out", sogg="Mark"),
        ev(
            "buy",
            "buy bread",
            e_testa=False,
            tempo="non_finito",
            fattualita="NON_FATTUALE",
            sogg="Mark",
            extra_args=[ogg("bread")],
        ),
    ],
    [rel(1, 0, "SCOPO")],
)
add(
    "eval-097",
    "en",
    "Mark did not arrive on time.",
    ["state"],
    [
        ev(
            "arrive",
            "did not arrive on time",
            polarita_negata=True,
            sogg="Mark",
        )
    ],
)
add(
    "eval-098",
    "en",
    "Mark knocked every morning.",
    ["imperfect"],
    [
        ev(
            "knock",
            "knocked every morning",
            tempo="passato",
            iterativo=True,
            sogg="Mark",
        )
    ],
)
add(
    "eval-099",
    "en",
    "Mark was tired, but he kept walking.",
    ["contrast", "state", "imperfect"],
    [
        ev("be", "was tired", tempo="imperfetto", sogg="Mark"),
        ev(
            "keep",
            "kept walking",
            sogg="he",
            sogg_tipo="pronome",
        ),
    ],
    [rel(0, 1, "CONTRASTO")],
)
add(
    "eval-100",
    "en",
    "With more time, Mark would have finished the work.",
    ["modal", "condition"],
    [
        ev(
            "finish",
            "would have finished the work",
            tempo="passato",
            modalita="ipotetico",
            fattualita="IPOTETICO",
            sogg="Mark",
            extra_args=[ogg("the work")],
        )
    ],
)

# extras to sit comfortably in 95–110
add(
    "eval-101",
    "it",
    "Il Dr. Rossi pagò 12.50 euro e partì.",
    ["abbreviation", "decimal", "coordinated_predicates"],
    [
        ev(
            "pagare",
            "pagò 12.50 euro",
            sogg="Il Dr. Rossi",
            extra_args=[ogg("12.50 euro")],
        ),
        ev("partire", "partì", sogg="Il Dr. Rossi"),
    ],
    [rel(0, 1, "SEQUENZA", "meets")],
)
add(
    "eval-102",
    "en",
    "Dr. Smith paid 12.50 euros and left.",
    ["abbreviation", "decimal", "coordinated_predicates"],
    [
        ev(
            "pay",
            "paid 12.50 euros",
            sogg="Dr. Smith",
            extra_args=[ogg("12.50 euros")],
        ),
        ev("leave", "left", sogg="Dr. Smith"),
    ],
    [rel(0, 1, "SEQUENZA", "meets")],
)
add(
    "eval-103",
    "it",
    "Dopo che Marco partì, Anna chiuse la porta.",
    ["cause"],
    [
        ev("partire", "partì", e_testa=False, sogg="Marco"),
        ev(
            "chiudere",
            "chiuse la porta",
            sogg="Anna",
            extra_args=[ogg("la porta")],
        ),
    ],
    [rel(0, 1, "PRECEDE", "before")],
)
add(
    "eval-104",
    "en",
    "After Mark left, Anna closed the door.",
    ["cause"],
    [
        ev("leave", "left", e_testa=False, sogg="Mark"),
        ev(
            "close",
            "closed the door",
            sogg="Anna",
            extra_args=[ogg("the door")],
        ),
    ],
    [rel(0, 1, "PRECEDE", "before")],
)


def locate_span(text: str, span: str, used: list[tuple[int, int]]) -> tuple[int, int]:
    start = 0
    while True:
        i = text.find(span, start)
        if i < 0:
            raise ValueError(f"span not found: {span!r} in {text!r}")
        end = i + len(span)
        if (i, end) not in used:
            used.append((i, end))
            return i, end
        start = i + 1


def materialize(raw: dict) -> dict:
    text = raw["text"]
    sent_texts = raw["sentences"]
    if not sent_texts:
        sent_texts = [text]
        tipi = ["narrativa"]
    else:
        tipi = raw["tipi"] or ["narrativa"] * len(sent_texts)

    sentences = []
    cursor = 0
    for i, st in enumerate(sent_texts):
        pos = text.find(st, cursor)
        if pos < 0:
            raise ValueError(f"{raw['id']}: sentence not found {st!r}")
        end = pos + len(st)
        sentences.append(
            {
                "indice": i,
                "offset_inizio": pos,
                "offset_fine": end,
                "testo": st,
                "tipo": tipi[i] if i < len(tipi) else "narrativa",
            }
        )
        cursor = end

    used: list[tuple[int, int]] = []
    eventi = []
    for i, event in enumerate(raw["events"]):
        start, end = locate_span(text, event["span"], used)
        item = dict(event)
        item["indice"] = i
        item["offset_inizio"] = start
        item["offset_fine"] = end
        eventi.append(item)

    return {
        "id": raw["id"],
        "lang": raw["lang"],
        "text": text,
        "phenomena": raw["phenomena"],
        "gold": {
            "sentences": sentences,
            "eventi": eventi,
            "relazioni": raw["rels"],
        },
    }


def main() -> None:
    items = [materialize(raw) for raw in RAW]
    OUT.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    n_it = sum(1 for x in items if x["lang"] == "it")
    n_en = sum(1 for x in items if x["lang"] == "en")
    print(f"wrote {len(items)} items ({n_it} it, {n_en} en) -> {OUT}")


if __name__ == "__main__":
    main()
