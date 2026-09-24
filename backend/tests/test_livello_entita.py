"""Test per il livello entità."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models.event_graph import (
    EntitaKernelCategoria,
    EntitaKernelClassificata,
    LivelloEntitaResult,
    MenzioneRisolta,
    EventoRisolto,
    ArgomentoRisolto,
)
from app.pipeline.event_graph.livello_entita import (
    estrai_livello_entita,
    persist_livello_entita
)


@pytest.mark.asyncio
async def test_estrai_livello_entita_temporale():
    """Test per la classificazione delle menzioni con arco TEMPO."""
    
    # Crea mock di eventi e menzioni
    evento1 = EventoRisolto(
        id="event1",
        lemma="evento1"
    )
    
    evento2 = EventoRisolto(
        id="event2", 
        lemma="evento2"
    )
    
    menzione_temporale = MenzioneRisolta(
        id="menz_temporale",
        forma="ieri"
    )
    
    menzione_sogg_ogg = MenzioneRisolta(
        id="menz_sogg_ogg",
        forma="Mario"
    )
    
    # Simula un argomento TEMPO (ruolo argomentale, non un arco causale)
    evento1.argomenti = [
        ArgomentoRisolto(ruolo="TEMPO", menzione_id="menz_temporale")
    ]

    # Simula un argomento SOGG
    evento2.argomenti = [
        ArgomentoRisolto(ruolo="SOGG", menzione_id="menz_sogg_ogg")
    ]
    
    menzioni = {
        "menz_temporale": menzione_temporale,
        "menz_sogg_ogg": menzione_sogg_ogg
    }
    
    # Mock della funzione LLM (non viene chiamata per le menzioni con TEMPO)
    mock_call_structured = AsyncMock()
    
    # Chiama la funzione principale
    result = await estrai_livello_entita(
        [evento1, evento2],
        menzioni,
        job_id="test_job",
        call_structured=mock_call_structured
    )
    
    # Verifica che le classificazioni siano corrette: TEMPO (1) + Fatti per
    # ciascuno dei 2 eventi/fatti passati (classificazione automatica, non
    # solo quello con l'argomento SOGG). "Evento" resta libero per l'LLM.
    assert len(result.classificazioni) == 3

    # Trova la classificazione temporale
    class_temporale = next((c for c in result.classificazioni if c.menzione_id == "menz_temporale"), None)
    assert class_temporale is not None
    assert class_temporale.categoria == EntitaKernelCategoria.Temporale

    # Trova le classificazioni fatti (entrambi gli eventi/fatti)
    class_evento1 = next((c for c in result.classificazioni if c.menzione_id == "event1"), None)
    assert class_evento1 is not None
    assert class_evento1.categoria == EntitaKernelCategoria.Fatti

    class_evento2 = next((c for c in result.classificazioni if c.menzione_id == "event2"), None)
    assert class_evento2 is not None
    assert class_evento2.categoria == EntitaKernelCategoria.Fatti


@pytest.mark.asyncio 
async def test_estrai_livello_entita_sogg_ogg():
    """Test per la classificazione delle menzioni con SOGG/OGG tramite LLM."""
    
    # Crea mock di eventi e menzioni
    evento = EventoRisolto(
        id="event1",
        lemma="evento1"
    )
    
    menzione_sogg_ogg = MenzioneRisolta(
        id="menz_sogg_ogg",
        forma="Mario"
    )
    
    # Simula un argomento SOGG ma senza TEMPO
    evento.argomenti = [
        ArgomentoRisolto(ruolo="SOGG", menzione_id="menz_sogg_ogg")
    ]
    
    menzioni = {
        "menz_sogg_ogg": menzione_sogg_ogg
    }
    
    # Mock della funzione LLM che restituisce una classificazione
    mock_call_structured = AsyncMock()
    mock_result = MagicMock()
    mock_result.classificazioni = [
        EntitaKernelClassificata(
            menzione_id="menz_sogg_ogg",
            categoria=EntitaKernelCategoria.Agente
        )
    ]
    mock_call_structured.return_value = mock_result
    
    # Chiama la funzione principale
    result = await estrai_livello_entita(
        [evento],
        menzioni,
        job_id="test_job", 
        call_structured=mock_call_structured
    )
    
    # Verifica che il risultato sia corretto
    assert len(result.classificazioni) == 2
    
    # Trova la classificazione LLM
    class_llm = next((c for c in result.classificazioni if c.menzione_id == "menz_sogg_ogg"), None)
    assert class_llm is not None
    assert class_llm.categoria == EntitaKernelCategoria.Agente


@pytest.mark.asyncio
async def test_persist_livello_entita():
    """Test per la persistenza delle classificazioni."""
    
    # Crea mock di risultato
    result = LivelloEntitaResult(
        classificazioni=[
            EntitaKernelClassificata(
                menzione_id="menz1",
                categoria=EntitaKernelCategoria.Agente
            ),
            EntitaKernelClassificata(
                menzione_id="event1", 
                categoria=EntitaKernelCategoria.Evento
            )
        ]
    )
    
    # Mock della sessione Neo4j
    mock_session = AsyncMock()
    
    # Chiama la funzione di persistenza
    await persist_livello_entita(mock_session, result, "test_doc")
    
    # Verifica che le query siano state chiamate correttamente
    assert mock_session.run.call_count == 2
    
    # Verifica che i parametri siano corretti
    calls = [call[0][0] for call in mock_session.run.call_args_list]
    expected_queries = [
        "MATCH (m) WHERE m.id = $menzione_id AND (m:Fatto OR m:Menzione) SET m.kernel_category = $categoria",
        "MATCH (m) WHERE m.id = $menzione_id AND (m:Fatto OR m:Menzione) SET m.kernel_category = $categoria"
    ]

    for i, query in enumerate(expected_queries):
        assert query in calls[i]