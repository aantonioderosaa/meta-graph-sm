"""Test completi per il livello entità con casi d'uso estesi."""

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
    persist_livello_entita,
    ENTITA_KERNEL_CATEGORIE
)


@pytest.mark.asyncio
async def test_estrai_livello_entita_completo():
    """Test completo per la classificazione delle entità con diversi casi."""
    
    # Crea mock di eventi e menzioni
    evento1 = EventoRisolto(
        id="event1",
        lemma="evento1"
    )
    
    evento2 = EventoRisolto(
        id="event2", 
        lemma="evento2"
    )
    
    # Menzioni con ruolo TEMPO (assegnazione diretta)
    menzione_temporale = MenzioneRisolta(
        id="menz_temporale",
        forma="ieri"
    )
    
    # Menzioni con SOGG/OGG (da classificare con LLM)
    menzione_sogg_ogg = MenzioneRisolta(
        id="menz_sogg_ogg",
        forma="Mario"
    )
    
    # Menzione senza ruolo specifico
    menzione_libera = MenzioneRisolta(
        id="menz_libera", 
        forma="casa"
    )
    
    # Simula argomenti TEMPO
    evento1.argomenti = [
        ArgomentoRisolto(ruolo="TEMPO", menzione_id="menz_temporale")
    ]

    # Simula argomenti SOGG/OGG
    evento2.argomenti = [
        ArgomentoRisolto(ruolo="SOGG", menzione_id="menz_sogg_ogg"),
        ArgomentoRisolto(ruolo="OGG", menzione_id="menz_libera"),
    ]
    
    menzioni = {
        "menz_temporale": menzione_temporale,
        "menz_sogg_ogg": menzione_sogg_ogg,
        "menz_libera": menzione_libera
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
        [evento1, evento2],
        menzioni,
        job_id="test_job",
        call_structured=mock_call_structured
    )
    
    # Verifica che tutte le classificazioni siano presenti
    assert len(result.classificazioni) == 4  # Tempo + Fatti(x2) + LLM

    # Trova la classificazione temporale
    class_temporale = next((c for c in result.classificazioni if c.menzione_id == "menz_temporale"), None)
    assert class_temporale is not None
    assert class_temporale.categoria == EntitaKernelCategoria.Temporale

    # Trova la classificazione fatto (il fatto viene automaticamente classificato)
    class_evento = next((c for c in result.classificazioni if c.menzione_id == "event2"), None)
    assert class_evento is not None
    assert class_evento.categoria == EntitaKernelCategoria.Fatti
    
    # Trova la classificazione LLM
    class_llm = next((c for c in result.classificazioni if c.menzione_id == "menz_sogg_ogg"), None)
    assert class_llm is not None
    assert class_llm.categoria == EntitaKernelCategoria.Agente


@pytest.mark.asyncio 
async def test_estrai_livello_entita_no_menzioni():
    """Test per la gestione di input senza menzioni."""
    
    # Chiama con liste vuote
    result = await estrai_livello_entita(
        [],
        {},
        job_id="test_job"
    )
    
    # Verifica che il risultato sia corretto
    assert len(result.classificazioni) == 0


@pytest.mark.asyncio 
async def test_estrai_livello_entita_evento_senza_menioni():
    """Test per la classificazione di eventi senza menzioni."""
    
    evento = EventoRisolto(
        id="event1",
        lemma="evento1"
    )
    
    # Chiama la funzione principale
    result = await estrai_livello_entita(
        [evento],
        {},
        job_id="test_job"
    )
    
    # Verifica che ci sia una classificazione per il fatto
    assert len(result.classificazioni) == 1
    class_evento = result.classificazioni[0]
    assert class_evento.menzione_id == "event1"
    assert class_evento.categoria == EntitaKernelCategoria.Fatti


@pytest.mark.asyncio 
async def test_persist_livello_entita_completo():
    """Test completo per la persistenza delle classificazioni."""
    
    # Crea mock di risultato
    result = LivelloEntitaResult(
        classificazioni=[
            EntitaKernelClassificata(
                menzione_id="menz1",
                categoria=EntitaKernelCategoria.Agente
            ),
            EntitaKernelClassificata(
                menzione_id="event1",
                categoria=EntitaKernelCategoria.Fatti
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


def test_entita_kernel_categorie_count():
    """Verifica che ci siano esattamente 10 categorie kernel."""
    assert len(ENTITA_KERNEL_CATEGORIE) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])