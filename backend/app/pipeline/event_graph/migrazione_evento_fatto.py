"""Script di migrazione idempotente da :Evento a :Fatto.

Questo script può essere eseguito in qualsiasi momento per migrare i nodi 
esistenti che hanno il label :Evento al nuovo label :Fatto.
"""

async def migra_eventi_fatto(session):
    """Esegue la migrazione dei nodi da :Evento a :Fatto."""
    query = """
    MATCH (n:Evento) 
    SET n:Fatto 
    REMOVE n:Evento
    """
    
    try:
        result = await session.run(query)
        # Restituisce il numero di nodi migrati se necessario
        return result
    except Exception as e:
        print(f"Errore durante la migrazione: {e}")
        raise

# Esempio di utilizzo (in un contesto reale):
# await migra_eventi_fatto(session)