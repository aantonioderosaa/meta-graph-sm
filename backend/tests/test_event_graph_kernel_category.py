"""Test per il nuovo enum EntitaKernelCategoria e i modelli associati."""

import pytest
from app.models.event_graph import (
    EntitaKernelCategoria,
    EntitaKernelClassificata,
    LivelloEntitaResult
)


def test_entita_kernel_categoria_valori():
    """Verifica che l'enum EntitaKernelCategoria abbia esattamente i 10 valori richiesti."""
    # Verifica il numero di valori
    assert len(EntitaKernelCategoria) == 10

    # Verifica i singoli valori
    expected_values = [
        "Agente",
        "OggettoFisico",
        "Luogo",
        "Evento",
        "EntitaTemporale",
        "EntitaInformativa",
        "CostruttoSociale",
        "EntitaAstratta",
        "Temporale",
        "Fatti"
    ]
    
    assert [categoria.value for categoria in EntitaKernelCategoria] == expected_values


def test_entita_kernel_classificata():
    """Verifica il modello EntitaKernelClassificata."""
    # Test con confidenza
    classificazione = EntitaKernelClassificata(
        menzione_id="test_001",
        categoria=EntitaKernelCategoria.Agente,
        confidenza=0.95
    )
    
    assert classificazione.menzione_id == "test_001"
    assert classificazione.categoria == EntitaKernelCategoria.Agente
    assert classificazione.confidenza == 0.95
    
    # Test senza confidenza
    classificazione2 = EntitaKernelClassificata(
        menzione_id="test_002",
        categoria=EntitaKernelCategoria.Luogo
    )
    
    assert classificazione2.menzione_id == "test_002"
    assert classificazione2.categoria == EntitaKernelCategoria.Luogo
    assert classificazione2.confidenza is None


def test_livello_entita_result():
    """Verifica il modello LivelloEntitaResult."""
    # Test con una lista di classificazioni
    classificazione1 = EntitaKernelClassificata(
        menzione_id="test_001",
        categoria=EntitaKernelCategoria.Agente,
        confidenza=0.95
    )
    
    classificazione2 = EntitaKernelClassificata(
        menzione_id="test_002", 
        categoria=EntitaKernelCategoria.OggettoFisico
    )
    
    result = LivelloEntitaResult(
        classificazioni=[classificazione1, classificazione2]
    )
    
    assert len(result.classificazioni) == 2
    assert result.classificazioni[0].menzione_id == "test_001"
    assert result.classificazioni[1].menzione_id == "test_002"


def test_import_non_permesso():
    """Verifica che il modulo kernel non venga importato da event_graph.py."""
    with open("app/models/event_graph.py", "r") as f:
        content = f.read()

    assert "from app.models.kernel" not in content
    assert "from app.models import kernel" not in content
    assert "import app.models.kernel" not in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])