"""AC-B: stable mention identity (B1–B6). FakeSession + stubs. No Docker."""

from __future__ import annotations

from app.models.event_graph import (
    ArgomentoRisolto,
    ChunkFactsheet,
    EventoGrezzo,
    EventoRisolto,
    MenzioneRisolta,
    SottoGrafo,
)
from app.pipeline.event_graph.chunking_periods import preprocess_zona
from app.pipeline.event_graph.ids import content_hash, menzione_id
from app.pipeline.event_graph.mention_coref import risolvi_intra
from app.pipeline.event_graph.segmentation import _order_events
from app.pipeline.event_graph.text_norm import _normalize_referential, fold_text
from app.pipeline.event_graph.zona_segmentation import Zona


def _fs() -> ChunkFactsheet:
    return ChunkFactsheet(eventi=[], archi=[], quarantena=[])


def _menzione(
    forma: str,
    tipo_superficiale: str,
    *,
    mid: str,
    numero: str = "sing",
    genere: str = "masc",
    non_risolto: bool = True,
    documento: str = "doc-b",
    chunk_id: str = "chunk-b",
) -> MenzioneRisolta:
    return MenzioneRisolta(
        id=mid,
        forma=forma,
        forma_canonica=forma,
        numero=numero,  # type: ignore[arg-type]
        genere=genere,  # type: ignore[arg-type]
        tipo_superficiale=tipo_superficiale,  # type: ignore[arg-type]
        non_risolto=non_risolto,
        documento=documento,
        chunk_id=chunk_id,
    )


def _evento(
    lemma: str,
    frase_indice: int,
    ruoli: list[tuple[str, MenzioneRisolta]],
    *,
    event_id: str | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id or lemma,
        lemma=lemma,
        frase_indice=frase_indice,
        posizione_chunk=frase_indice,
        posizione_doc=0,
        argomenti=[
            ArgomentoRisolto(ruolo=ruolo, menzione_id=mention.id)  # type: ignore[arg-type]
            for ruolo, mention in ruoli
        ],
    )


def _seed(sotto: SottoGrafo, *menzioni: MenzioneRisolta) -> None:
    for mention in menzioni:
        sotto.menzioni[mention.id] = mention


def _grezzo(indice: int, lemma: str, span: str) -> EventoGrezzo:
    return EventoGrezzo(
        indice=indice,
        lemma=lemma,
        span=span,
        tempo="passato",
        segmentazione="principale_finita",
        polarita_negata=False,
        modalizzato=False,
        iterativo=False,
        ruolo_se="nessuno",
        completiva_di=None,
        classe_verbo_reggente="nessuna",
        finale=False,
        frase_tipo="dichiarativa",
        marca_dialogo=False,
        frase_indice=0,
        avverbio_temporale_esplicito=False,
        connettivo_sequenziale_esplicito=False,
        argomenti=[],
        sogg_speciale="nessuno",
        e_testa=False,
        modalita="fattuale",
    )


def test_normalize_referential_and_fold():
    assert _normalize_referential("il Sole") == "sole"
    assert _normalize_referential("al Sole") == "sole"
    assert _normalize_referential("Il Sole, invece") == "sole"
    assert _normalize_referential("Sole") == "sole"
    assert _normalize_referential("il sole") == "sole"
    assert _normalize_referential("the Wind") == "wind"
    assert _normalize_referential("l'uomo") == "uomo"
    assert _normalize_referential("L\u2019uomo") == "uomo"
    assert _normalize_referential("il viandante") == "viandante"
    assert _normalize_referential("il vecchio Sole") == "vecchio sole"
    folded = fold_text("L\u2019uomo, \u201csentendo il vento\u201d")
    assert "'" in folded
    assert "\u2019" not in folded
    assert '"' in folded
    assert "\u201c" not in folded
    assert folded == fold_text(folded)


def test_sole_invece_matches_turno_del_sole():
    from app.pipeline.event_graph.mention_coref import _names_match

    assert _names_match("Il Sole, invece", "il turno del Sole")
    assert _names_match("il Sole", "il turno del Sole")


def test_b1_three_sole_forms_fuse_to_content_hash():
    expected = content_hash("sole")
    il_sole = _menzione("il Sole", "sn_comune", mid="occ-1")
    sole = _menzione("Sole", "nome_proprio", mid="occ-2")
    il_sole_lc = _menzione("il sole", "sn_comune", mid="occ-3")
    e1 = _evento("brillare", 0, [("SOGG", il_sole)], event_id="e1")
    e2 = _evento("scaldare", 1, [("SOGG", sole)], event_id="e2")
    e3 = _evento("vincere", 2, [("SOGG", il_sole_lc)], event_id="e3")
    sotto = SottoGrafo()
    _seed(sotto, il_sole, sole, il_sole_lc)

    risolvi_intra([e1, e2, e3], _fs(), sotto)

    assert e1.argomenti[0].menzione_id == expected
    assert e2.argomenti[0].menzione_id == expected
    assert e3.argomenti[0].menzione_id == expected
    assert len(sotto.menzioni) == 1
    fused = sotto.menzioni[expected]
    assert fused.id == expected
    assert fused.non_risolto is False


def test_b2_number_conflict_not_fused():
    sing = _menzione("il Sole", "sn_comune", mid="occ-sing", numero="sing", genere="masc")
    plur = _menzione("i Soli", "sn_comune", mid="occ-plur", numero="plur", genere="masc")
    e1 = _evento("brillare", 0, [("SOGG", sing)], event_id="e-sing")
    e2 = _evento("cadere", 1, [("SOGG", plur)], event_id="e-plur")
    sotto = SottoGrafo()
    _seed(sotto, sing, plur)

    risolvi_intra([e1, e2], _fs(), sotto)

    assert e1.argomenti[0].menzione_id != e2.argomenti[0].menzione_id
    assert len(sotto.menzioni) == 2


def test_b3_pronoun_two_compatible_antecedents_unresolved():
    sole = _menzione("il Sole", "sn_comune", mid="m-sole", numero="sing", genere="masc")
    vento = _menzione("il Vento", "sn_comune", mid="m-vento", numero="sing", genere="masc")
    lo = _menzione("lo", "pronome", mid="m-lo", numero="sing", genere="masc")
    e1 = _evento("brillare", 0, [("SOGG", sole)], event_id="e-sole")
    e2 = _evento("soffiare", 0, [("SOGG", vento)], event_id="e-vento")
    e3 = _evento("vedere", 1, [("OGG", lo)], event_id="e-lo")
    sotto = SottoGrafo()
    _seed(sotto, sole, vento, lo)

    risolvi_intra([e1, e2, e3], _fs(), sotto)

    assert e3.argomenti[0].menzione_id == lo.id
    assert lo.non_risolto is True
    assert lo.id in sotto.menzioni
    assert lo.id != e1.argomenti[0].menzione_id
    assert lo.id != e2.argomenti[0].menzione_id


def test_b4_menzione_id_sn_comune_stable():
    first = menzione_id("il Sole", "sn_comune", "doc-a", "chunk-1", 0)
    second = menzione_id("il Sole", "sn_comune", "doc-b", "chunk-9", 7)
    assert first.id == second.id == content_hash("sole")
    assert first.non_risolto is False
    assert second.non_risolto is False
    proprio = menzione_id("Sole", "nome_proprio", "doc-a", "chunk-1", 0)
    assert proprio.id == first.id
    assert proprio.non_risolto is False
    pronome = menzione_id("lo", "pronome", "doc-a", "chunk-1", 0)
    assert pronome.non_risolto is True
    assert pronome.id != first.id
    assert pronome.id != menzione_id("lo", "pronome", "doc-a", "chunk-1", 1).id


def test_b5_curly_apostrophe_span_found_after_fold():
    testo = "L\u2019uomo, sentendo il vento, si strinse nel mantello."
    assert testo.find("L'uomo") < 0
    assert fold_text(testo).find("L'uomo") >= 0
    events = [
        _grezzo(0, "stringere", "si strinse nel mantello"),
        _grezzo(1, "sentire", "sentendo il vento"),
        _grezzo(2, "essere", "L'uomo"),
    ]
    ordered = _order_events(events, testo)
    lemmas = [event.lemma for event in ordered]
    assert "sentire" in lemmas
    assert lemmas.index("essere") < lemmas.index("sentire")
    assert lemmas.index("sentire") < lemmas.index("stringere")

    zona = Zona(
        id="z-b5",
        documento="doc-b5",
        offset_inizio=0,
        offset_fine=len(testo),
        ordinale=0,
        testo=testo,
    )
    units = preprocess_zona(zona)
    assert units
    assert "\u2019" not in units[0].testo
    assert "L'uomo" in units[0].testo
    assert "sentendo il vento" in units[0].testo


def test_b6_uomo_and_viandante_stay_distinct():
    uomo = _menzione("l'uomo", "sn_comune", mid="occ-uomo")
    viandante = _menzione("il viandante", "sn_comune", mid="occ-viandante")
    e1 = _evento("camminare", 0, [("SOGG", uomo)], event_id="e-uomo")
    e2 = _evento("stringere", 1, [("SOGG", viandante)], event_id="e-viandante")
    sotto = SottoGrafo()
    _seed(sotto, uomo, viandante)

    risolvi_intra([e1, e2], _fs(), sotto)

    assert e1.argomenti[0].menzione_id != e2.argomenti[0].menzione_id
    assert len(sotto.menzioni) == 2
    ids = set(sotto.menzioni)
    assert content_hash("uomo") in ids
    assert content_hash("viandante") in ids
