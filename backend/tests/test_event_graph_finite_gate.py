"""AC-A: finite-verb gate (A1–A11). FakeSession + factsheet stubs. No Docker."""

from __future__ import annotations

import pytest

from app.models.event_graph import (
    ArgomentoGrezzo,
    EventoGrezzo,
    EventoRisolto,
    FraseFactsheet,
    PredicatoNonFinito,
)
from app.pipeline.event_graph.chunking_periods import UnitaTesto
from app.pipeline.event_graph.extraction import (
    _apply_remaining_violations,
    _finite_gate,
    evaluate_checklist,
    estrai_frase,
)
from app.pipeline.event_graph.factuality import applica


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.runs = []

    async def run(self, query, parameters=None, **params):
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **params}
        else:
            merged = dict(params)
        self.runs.append((query, merged))

        class R:
            def single(self_inner):
                return self.rows[0] if self.rows else None

            def data(self_inner):
                return self.rows

        return R()


def _sogg(forma: str = "il Vento") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="SOGG",
        forma=forma,
        tipo_superficiale="sn_comune",
        span=forma,
    )


def _grezzo(indice: int = 0, **overrides) -> EventoGrezzo:
    payload = {
        "indice": indice,
        "lemma": "arrivare",
        "span": "arrivò",
        "tempo": "passato",
        "segmentazione": "principale_finita",
        "polarita_negata": False,
        "modalizzato": False,
        "iterativo": False,
        "ruolo_se": "nessuno",
        "completiva_di": None,
        "classe_verbo_reggente": "nessuna",
        "finale": False,
        "frase_tipo": "dichiarativa",
        "marca_dialogo": False,
        "frase_indice": 0,
        "avverbio_temporale_esplicito": False,
        "connettivo_sequenziale_esplicito": False,
        "argomenti": [_sogg()],
        "sogg_speciale": "nessuno",
        "e_testa": False,
        "modalita": "fattuale",
    }
    payload.update(overrides)
    return EventoGrezzo(**payload)


def _sheet(*eventi: EventoGrezzo, **kwargs) -> FraseFactsheet:
    return FraseFactsheet(
        eventi=list(eventi),
        archi=kwargs.get("archi", []),
        quarantena=kwargs.get("quarantena", []),
        predicati_non_finiti=kwargs.get("predicati_non_finiti", []),
    )


def _unita(testo: str) -> UnitaTesto:
    return UnitaTesto(
        testo=testo,
        offset_inizio=0,
        offset_fine=len(testo),
        tipo="narrativa",
        connettivo_confine=None,
        zona_id="z-gate",
        indice=0,
    )


def _install_stub(monkeypatch, handler):
    async def stub(
        system_prompt,
        user_prompt,
        response_model,
        temperature=0,
        job_id=None,
    ):
        return await handler(
            system_prompt, user_prompt, response_model, temperature, job_id
        )

    monkeypatch.setattr(
        "app.pipeline.event_graph.extraction.call_structured", stub
    )


def _fattualita(event: EventoGrezzo) -> str | None:
    resolved = EventoRisolto(
        id=f"e-{event.indice}",
        lemma=event.lemma,
        tempo=event.tempo,
        modalita=event.modalita,
        modalizzato=event.modalizzato,
        modalizzato_forma=event.modalizzato_forma,
        polarita_negata=event.polarita_negata,
        ruolo_se=event.ruolo_se,
        frase_tipo=event.frase_tipo,
        finale=event.finale,
        completiva_di=event.completiva_di,
        classe_verbo_reggente=event.classe_verbo_reggente,
        segmentazione=event.segmentazione,
    )
    applica([resolved], None)
    return resolved.fattualita


def test_a1_aspectual_collapse_soffiare():
    testo = "Il Vento cominciò a soffiare."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="cominciare",
            span="cominciò",
            tempo="passato",
            segmentazione="principale_finita",
            e_testa=True,
        ),
        _grezzo(
            1,
            lemma="soffiare",
            span="soffiare",
            tempo="non_finito",
            segmentazione="infinitiva",
            completiva_di=0,
            e_testa=False,
        ),
    )
    gated = _finite_gate(sheet, testo)
    assert len(gated.eventi) == 1
    child = gated.eventi[0]
    assert child.lemma == "soffiare"
    assert child.tempo == "passato"
    assert not any(event.lemma == "cominciare" for event in gated.eventi)
    assert any("assorbito" in item.motivo for item in gated.quarantena)


def test_a2_coordinated_infinitives_inherit_tense():
    testo = "Il Sole cominciò a brillare e a scaldare."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="cominciare",
            span="cominciò",
            tempo="passato",
            segmentazione="principale_finita",
            e_testa=True,
        ),
        _grezzo(
            1,
            lemma="brillare",
            span="brillare",
            tempo="non_finito",
            segmentazione="infinitiva",
            completiva_di=0,
        ),
        _grezzo(
            2,
            lemma="scaldare",
            span="scaldare",
            tempo="non_finito",
            segmentazione="infinitiva",
            completiva_di=0,
        ),
    )
    gated = _finite_gate(sheet, testo)
    lemmas = {event.lemma for event in gated.eventi}
    assert lemmas == {"brillare", "scaldare"}
    assert len(gated.eventi) == 2
    assert all(event.tempo == "passato" for event in gated.eventi)


def test_a3_volere_vincere_volitivo_non_fattuale():
    testo = "Il Vento voleva vincere."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="volere",
            span="voleva",
            tempo="passato",
            segmentazione="principale_finita",
            modalita="volitivo",
            modalizzato=True,
            modalizzato_forma="voleva",
            e_testa=True,
        ),
        _grezzo(
            1,
            lemma="vincere",
            span="vincere",
            tempo="non_finito",
            segmentazione="infinitiva",
            completiva_di=0,
        ),
    )
    gated = _finite_gate(sheet, testo)
    assert len(gated.eventi) == 1
    child = gated.eventi[0]
    assert child.lemma == "vincere"
    assert child.modalita == "volitivo"
    assert _fattualita(child) == "NON_FATTUALE"


def test_a4_dovere_arrendere_passato_fattuale():
    testo = "Il Vento dovette arrendersi."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="dovere",
            span="dovette",
            tempo="passato",
            segmentazione="principale_finita",
            modalita="deontico",
            modalizzato=True,
            modalizzato_forma="dovette",
            e_testa=True,
        ),
        _grezzo(
            1,
            lemma="arrendersi",
            span="arrendersi",
            tempo="non_finito",
            segmentazione="infinitiva",
            completiva_di=0,
        ),
    )
    gated = _finite_gate(sheet, testo)
    assert len(gated.eventi) == 1
    child = gated.eventi[0]
    assert child.lemma == "arrendere"
    assert child.tempo == "passato"
    assert child.modalita == "fattuale"
    assert _fattualita(child) == "FATTUALE"


def test_a5_free_gerund_to_predicati_non_finiti():
    testo = "Sentendo il vento, l'uomo strinse il mantello."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="stringere",
            span="strinse",
            tempo="passato",
            segmentazione="principale_finita",
            e_testa=True,
            argomenti=[_sogg("l'uomo")],
        ),
        _grezzo(
            1,
            lemma="sentire",
            span="sentendo",
            tempo="non_finito",
            segmentazione="gerundiva",
            completiva_di=None,
            e_testa=False,
        ),
    )
    gated = _finite_gate(sheet, testo)
    assert all(event.lemma != "sentire" for event in gated.eventi)
    assert not any(event.span == "sentendo" for event in gated.eventi)
    assert len(gated.predicati_non_finiti) == 1
    pred = gated.predicati_non_finiti[0]
    assert isinstance(pred, PredicatoNonFinito)
    assert pred.forma_verbale == "gerundio"
    testa = next(event for event in gated.eventi if event.e_testa)
    assert pred.governo_indice == testa.indice


def test_a6_adverb_to_quarantena_evento_spurio():
    testo = "Il Vento soffiò lentamente."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="lentamente",
            span="lentamente",
            tempo="passato",
            segmentazione="principale_finita",
            e_testa=True,
        )
    )
    gated = _finite_gate(sheet, testo)
    assert gated.eventi == []
    assert any(item.motivo == "evento spurio" for item in gated.quarantena)


def test_a7_gnomic_finite_kept():
    testo = "La gentilezza è più efficace della forza."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="essere",
            span="è più efficace della forza",
            tempo="presente",
            segmentazione="principale_finita",
            e_testa=True,
            argomenti=[_sogg("La gentilezza")],
        )
    )
    gated = _finite_gate(sheet, testo)
    assert len(gated.eventi) == 1
    assert gated.eventi[0].lemma == "essere"
    assert gated.eventi[0].tempo == "presente"


def test_a8_signal_conflict_not_coined():
    testo = "Cadendo la sera, il vento soffiò."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="cadere",
            span="Cadendo",
            tempo="passato",
            segmentazione="gerundiva",
            e_testa=True,
        )
    )
    gated = _finite_gate(sheet, testo)
    assert gated.eventi == []
    assert not any(event.lemma == "cadere" for event in gated.eventi)


def test_a9_irregular_tolgere_to_togliere():
    testo = "L'uomo cominciò a togliersi il mantello."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="cominciare",
            span="cominciò",
            tempo="passato",
            segmentazione="principale_finita",
            e_testa=True,
        ),
        _grezzo(
            1,
            lemma="tolgere",
            span="togliersi",
            tempo="non_finito",
            segmentazione="infinitiva",
            completiva_di=0,
        ),
    )
    gated = _finite_gate(sheet, testo)
    assert len(gated.eventi) == 1
    assert gated.eventi[0].lemma == "togliere"


def test_a10_inflected_lemma_violation_then_quarantena():
    testo = "Mario disse la verità."
    event = _grezzo(
        0,
        lemma="disse",
        span="disse",
        tempo="passato",
        segmentazione="principale_finita",
        e_testa=True,
        argomenti=[_sogg("Mario")],
    )
    sheet = _sheet(event)
    violations = evaluate_checklist(sheet, testo=testo)
    assert any(item.motivo == "lemma non infinito" for item in violations)
    dropped = _apply_remaining_violations(sheet, violations)
    assert dropped.eventi == []
    assert any(
        "lemma non infinito" in item.motivo or "spurio" in item.motivo
        for item in dropped.quarantena
    )


@pytest.mark.asyncio
async def test_a10_stub_correction_unchanged_quarantena(monkeypatch):
    testo = "Mario disse la verità."
    event = _grezzo(
        0,
        lemma="disse",
        span="disse",
        tempo="passato",
        segmentazione="principale_finita",
        e_testa=True,
        argomenti=[_sogg("Mario")],
    )
    raw = _sheet(event)

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return raw

    _install_stub(monkeypatch, handler)
    session = FakeSession()
    result = await estrai_frase(_unita(testo), session=session)
    assert all(item.lemma != "disse" for item in result.eventi)
    assert result.quarantena
    assert any(
        "lemma non infinito" in item.motivo or "spurio" in item.motivo
        for item in result.quarantena
    )


def test_a2_si_tolse_finale_leftover_stays_fattuale():
    testo = "L'uomo si tolse il mantello."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="togliere",
            span="si tolse",
            tempo="passato",
            segmentazione="principale_finita",
            e_testa=True,
            finale=True,
            completiva_di=3,
            classe_verbo_reggente="non_fattivo",
            argomenti=[_sogg("L'uomo")],
        )
    )
    gated = _finite_gate(sheet, testo)
    assert len(gated.eventi) == 1
    child = gated.eventi[0]
    assert child.lemma == "togliere"
    assert child.finale is False
    assert child.completiva_di is None
    assert _fattualita(child) == "FATTUALE"


def test_a11_finite_gate_idempotent():
    testo = "Il Vento cominciò a soffiare."
    sheet = _sheet(
        _grezzo(
            0,
            lemma="cominciare",
            span="cominciò",
            tempo="passato",
            segmentazione="principale_finita",
            e_testa=True,
        ),
        _grezzo(
            1,
            lemma="soffiare",
            span="soffiare",
            tempo="non_finito",
            segmentazione="infinitiva",
            completiva_di=0,
        ),
    )
    once = _finite_gate(sheet, testo)
    twice = _finite_gate(once, testo)
    assert once.model_dump() == twice.model_dump()
