"""Genera registros FICTICIOS de demostración. No se ejecuta automáticamente."""
import argparse
from pathlib import Path
from db import Repository
from core import validate_ticket

SEEDS = [
    ("Sin conexión a internet en el laboratorio", "Las estaciones 01 a 12 dejaron de tener acceso a la red durante la clase de programación.", "Red e internet", "Laboratorio A", "estudiante-07", 3, 3),
    ("Proyector sin señal de video", "El proyector del aula multimedia muestra una pantalla azul aunque el equipo está encendido.", "Periféricos", "Aula multimedia", "docente-04", 2, 2),
    ("Error al abrir software de edición", "La aplicación de diseño se cierra al iniciar en cuatro equipos y no permite guardar trabajos.", "Software", "Laboratorio B", "estudiante-13", 2, 3),
    ("Teclado con teclas sin respuesta", "En el equipo 08 varias teclas no registran pulsaciones y dificultan completar prácticas.", "Hardware", "Biblioteca", "tecnico-02", 1, 2),
    ("Lentitud al ingresar a la plataforma", "Se detecta un tiempo de carga elevado en dos computadoras durante el acceso a materiales.", "Otro", "Laboratorio A", "estudiante-02", 2, 1),
]

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(Path(__file__).parent / 'data' / 'aulafix.db'))
    args = ap.parse_args()
    repo=Repository(args.db)
    if repo.metrics()['total']:
        print('La base no está vacía; para evitar duplicados no se insertaron datos.')
    else:
        for i,row in enumerate(SEEDS):
            d=dict(zip(('title','description','category','location','reporter','impact','urgency'),row))
            clean,errors=validate_ticket(d)
            assert not errors, errors
            ticket_id=repo.create_ticket(clean)
            if i == 1:
                repo.change_status(ticket_id,'En proceso','El técnico revisó el proyector y verificó el cableado HDMI.')
            if i == 3:
                repo.change_status(ticket_id,'En proceso','Se detectó desgaste físico en el teclado del equipo.')
                repo.change_status(ticket_id,'Resuelto','Se reemplazó el teclado y se confirmó que todas las teclas funcionan.')
        print('Insertados cinco reportes ficticios para la demostración.')
