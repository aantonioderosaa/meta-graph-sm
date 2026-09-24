"""Livello entità: classificazione delle menzioni secondo il kernel vocabolario (piano sez. 41)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from app.models.event_graph import (
    EntitaKernelCategoria,
    EntitaKernelClassificata,
    LivelloEntitaResult,
    MenzioneRisolta,
)
from app.pipeline.event_graph.infra.llm import call_structured as _call_structured_default

if TYPE_CHECKING:
    from app.models.event_graph import EventoRisolto

T = TypeVar("T")

# Vocabolario locale per le 9 categorie + "Fatti" (10 totali)
ENTITA_KERNEL_CATEGORIE = [
    EntitaKernelCategoria.Agente,
    EntitaKernelCategoria.OggettoFisico,
    EntitaKernelCategoria.Luogo,
    EntitaKernelCategoria.Evento,
    EntitaKernelCategoria.EntitaTemporale,
    EntitaKernelCategoria.EntitaInformativa,
    EntitaKernelCategoria.CostruttoSociale,
    EntitaKernelCategoria.EntitaAstratta,
    EntitaKernelCategoria.Temporale,
    EntitaKernelCategoria.Fatti
]

# Prompt per il classificatore LLM
SYSTEM_PROMPT = """Sei un esperto di classificazione semantica. Classifica le entità estratte da testi narrativi in base alle loro categorie kernel.

Le 9 categorie semantiche sono:
- Agente: persone fisiche o organizzazioni (es. "Mario", "la polizia")
- OggettoFisico: oggetti materiali (es. "una palla", "un libro")  
- Luogo: località geografiche o spaziali (es. "Roma", "il parco")
- Evento: eventi narrativi o azioni (es. "la festa", "l'incendio")
- EntitaTemporale: elementi temporali specifici (es. "ieri", "dopo cena")
- EntitaInformativa: informazioni, documenti o contenuti (es. "il rapporto", "la lettera")
- CostruttoSociale: concetti sociali o istituzionali (es. "la legge", "la giustizia")
- EntitaAstratta: concetti astratti o filosofici (es. "amore", "giustizia")
- Temporale: menzioni che sono state identificate con ruolo argomentale TEMPO in un arco di relazione

La categoria "Fatti" è riservata a quelle menzioni che sono nodi :Fatto.

Criteri per classificare:
1. Se la menzione ha un arco TEMPO (ruolo argomentale), assegna categoria "Temporale" senza LLM
2. Per le menzioni con SOGG/OGG, usa il LLM per classificarle tra le 9 categorie sopra
3. Le menzioni che sono nodi Fatto devono essere classificate come "Fatti"
4. Non creare nuove categorie o modificare quelle esistenti

Per ogni menzione, rispondi con un JSON compatibile con lo schema fornito.
"""

USER_PROMPT_TEMPLATE = """Classifica le seguenti menzioni in base alle loro categorie kernel:

Menzioni:
{menzioni}

Istruzioni per la classificazione:
1. Se una menzione ha almeno un arco TEMPO, assegna automaticamente categoria "Temporale"
2. Per le menzioni con ruolo SOGG/OGG che non hanno archi TEMPO, usa il LLM per classificarle tra {categorie}
3. Le menzioni che sono nodi Fatto devono essere classificate come "Fatti"
4. Usa solo le categorie indicate sopra
5. Nessuna menzione può avere più di una categoria

Rispondi con un JSON compatibile con questo schema:
{schema}"""

# Schema per la risposta LLM (Pydantic)
class EntitaClassificata(BaseModel):
    """Una classificazione singola delle entità."""
    menzione_id: str
    categoria: EntitaKernelCategoria

class LivelloEntitaResultLLM(BaseModel):
    """Il risultato del classificatore di livello entità (schema per LLM)."""
    classificazioni: list[EntitaClassificata]


async def estrai_livello_entita(
    eventi: list[EventoRisolto],
    menzioni: dict[str, MenzioneRisolta],
    job_id: str | None = None,
    call_structured=None,
) -> LivelloEntitaResult:
    """
    Classifica le menzioni secondo il kernel vocabolario.
    
    Args:
        eventi: Lista di eventi risolti
        menzioni: Dizionario delle menzioni risolte
        job_id: ID del job per tracciamento
        call_structured: Funzione di chiamata LLM (per testing)
        
    Returns:
        Risultato con le classificazioni per ogni menzione
    """
    if not menzioni and not eventi:
        return LivelloEntitaResult(classificazioni=[])

    # Prepariamo il client LLM se non fornito (il parametro `call_structured`
    # ombreggia l'import a livello di modulo per l'intera funzione: senza
    # l'alias `_call_structured_default` non c'è modo di raggiungere la
    # funzione reale quando il chiamante passa call_structured=None).
    call_fn = call_structured or _call_structured_default
    
    # Raccogliamo tutte le menzioni che possono essere classificate con LLM
    menzioni_da_classificare = []
    menzioni_temporali = []  # Menzioni con ruolo TEMPO
    
    # Troviamo le menzioni con arco TEMPO
    for evento in eventi:
        if not evento.id:
            continue
            
        for argomento in evento.argomenti:
            if argomento.ruolo == "TEMPO" and argomento.menzione_id:
                menzioni_temporali.append(argomento.menzione_id)
    
    # Classificazioni per le menzioni con ruolo TEMPO (assegnazione diretta)
    classificazioni_temporali = [
        EntitaKernelClassificata(
            menzione_id=menz_id,
            categoria=EntitaKernelCategoria.Temporale
        )
        for menz_id in menzioni_temporali
        if menz_id in menzioni
    ]
    
    # Classificazioni per i nodi Fatto (assegnazione diretta a "Fatti")
    classificazioni_fatti = []
    for evento in eventi:
        if not evento.id:
            continue
            
        # Verifichiamo che l'evento abbia un ID valido e una menzione associata
        # In questo contesto, le menzioni sono già risolte e collegate agli eventi
        classificazioni_fatti.append(
            EntitaKernelClassificata(
                menzione_id=evento.id,
                categoria=EntitaKernelCategoria.Fatti
            )
        )
    
    # Menzioni con SOGG/OGG ma senza TEMPO (per LLM)
    menzioni_sogg_ogg = []
    for menz_id, menz in menzioni.items():
        # Controlliamo se la menzione è associata a un evento con ruolo SOGG o OGG
        is_sogg_ogg = any(
            argomento.ruolo in ["SOGG", "OGG"] and argomento.menzione_id == menz_id
            for evento in eventi
            for argomento in evento.argomenti
        )
        
        # Se la menzione non ha un arco TEMPO, è candidata per LLM
        if is_sogg_ogg and menz_id not in menzioni_temporali:
            menzioni_sogg_ogg.append(menz_id)
    
    classificazioni_llm = []
    
    # Se ci sono menzioni da classificare con LLM, chiamiamo il modello
    if menzioni_sogg_ogg:
        try:
            # Preparo le informazioni per il prompt
            menzioni_info = []
            for menz_id in menzioni_sogg_ogg:
                menzione = menzioni[menz_id]
                menzioni_info.append({
                    "id": menz_id,
                    "forma": menzione.forma,
                    "tipo_superficiale": menzione.tipo_superficiale
                })
            
            # Costruiamo il prompt utente con i dati specifici della menzione
            user_prompt = USER_PROMPT_TEMPLATE.format(
                menzioni=menzioni_info,
                categorie=", ".join([cat.value for cat in ENTITA_KERNEL_CATEGORIE[:-1]]),  # Escludiamo "Fatti"
                schema=LivelloEntitaResultLLM.model_json_schema()
            )
            
            # Chiamata al modello LLM
            result = await call_fn(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=LivelloEntitaResultLLM,
                temperature=0.1,  # Basso per consistenza
                job_id=job_id
            )
            
            classificazioni_llm = [
                EntitaKernelClassificata(
                    menzione_id=item.menzione_id,
                    categoria=item.categoria
                )
                for item in result.classificazioni
            ]
        except Exception as e:
            # In caso di errore, continuamos con le altre classificazioni
            print(f"Errore durante la classificazione LLM: {e}")
            
    # Uniamo tutte le classificazioni
    classificazioni_totali = (
        classificazioni_temporali + 
        classificazioni_fatti + 
        classificazioni_llm
    )
    
    return LivelloEntitaResult(classificazioni=classificazioni_totali)


async def persist_livello_entita(
    session,
    result: LivelloEntitaResult,
    doc_id: str,
) -> None:
    """
    Persiste le classificazioni delle entità nel database.
    
    Args:
        session: Sessione di database Neo4j
        result: Risultato della classificazione
        doc_id: ID del documento
    """
    if not result.classificazioni:
        return
        
    # Costruiamo un batch singolo per documento
    queries = []
    
    for classificazione in result.classificazioni:
        # menzione_id punta a un nodo :Menzione (SOGG/OGG/TEMPO) o :Fatto
        # (auto-classificazione "Fatti") — il match copre entrambi i label.
        query = (
            "MATCH (m) WHERE m.id = $menzione_id AND (m:Fatto OR m:Menzione) "
            "SET m.kernel_category = $categoria"
        )

        params = {
            "menzione_id": classificazione.menzione_id,
            "categoria": classificazione.categoria.value
        }

        queries.append((query, params))

    # Eseguiamo tutte le query in un batch
    for query, params in queries:
        try:
            await session.run(query, **params)
        except Exception as e:
            print(f"Errore durante la persistenza della classificazione: {e}")
            continue