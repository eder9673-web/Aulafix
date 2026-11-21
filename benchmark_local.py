"""Medición exploratoria del repositorio, NO prueba de carga ni SLA."""
import json
import statistics
import tempfile
import time
from pathlib import Path
from core import validate_ticket
from db import Repository

with tempfile.TemporaryDirectory() as tmp:
    repo=Repository(Path(tmp)/'benchmark.db')
    times=[]
    for i in range(100):
        form={'title':f'Falla ficticia número {i:03d} de conectividad',
              'description':'Reporte sintético de conectividad durante un ejercicio académico con datos inventados.',
              'category':'Red e internet','location':'Laboratorio A','reporter':'simulado-01','impact':2,'urgency':3}
        clean,errors=validate_ticket(form)
        assert not errors
        repo.create_ticket(clean)
    for i in range(50):
        start=time.perf_counter()
        repo.list_tickets('Red','Abierto')
        times.append((time.perf_counter()-start)*1000)
    print(json.dumps({'tickets_sinteticos':100,'consultas':50,
      'mediana_ms':round(statistics.median(times),3),
      'p95_ms':round(sorted(times)[int(0.95*(len(times)-1))],3),
      'interpretacion':'Medición local de SQLite, no incluye red ni navegador.'},ensure_ascii=False,indent=2))
