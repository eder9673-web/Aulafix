# AulaFix | Bitácora universitaria de incidencias TI

**Versión:** 1.0 académica · **Lenguaje:** Python 3.10 o posterior · **Base de datos:** SQLite · **Dependencias externas:** ninguna.

Sistema web local para registrar fallas de cómputo en espacios universitarios, asignar una prioridad transparente y conservar una bitácora de cambios de estado. Los datos de muestra son inventados. No representa un producto contratado ni está conectado a ninguna universidad.

## Inicio rápido

1. Descarga o extrae el ZIP. Abre una terminal dentro de la carpeta `aulafix_repo`.
2. Comprueba Python: `python --version` (en algunos equipos Windows es `py --version`).
3. Ejecuta: `python app.py` (Windows: `py app.py`).
4. Abre **http://127.0.0.1:8000** en el navegador.
5. Para detener el servidor presiona `Ctrl+C`.

La carpeta `data/` y la base `aulafix.db` se crean automáticamente al iniciar. La aplicación **solo escucha en tu equipo** mediante `127.0.0.1`.

### Agregar cinco casos de ejemplo

Con el servidor detenido, ejecuta `python demo_seed.py`. Luego reinicia `python app.py`. El script solo agrega ejemplos si no existen reportes. Para empezar de cero, detén el servidor y elimina `data/aulafix.db` (se perderán los reportes locales).

### Ejecución de pruebas

Desde la raíz del repositorio:

```bash
python -m unittest discover -s tests -v
```

Son 114 pruebas: 37 de reglas de negocio y valores límite, 17 de SQLite y concurrencia, 54 HTTP (seguridad local, máquina de estados y manejo de conexión) y 4 de arranque, más 2 que ejecutan `demo_seed.py` contra una base temporal para comprobar que no duplica datos. No utilizan la base de datos real de la aplicación: cada prueba crea una SQLite temporal que se borra al terminar. También se verificaron sin fugas de recursos: `python -W error::ResourceWarning -m unittest discover -s tests` termina en `OK`.

Cobertura de línea y rama (opcional; requiere `pip install -r requirements-dev.txt`):

```bash
python -m coverage run --branch --source=. --omit="tests/*,benchmark_local.py,demo_seed.py" -m unittest discover -s tests -t .
python -m coverage report -m
```

En Visual Studio Code, `.vscode/settings.json` ya deja configurado el panel *Testing* con `unittest`; `.vscode/launch.json` incluye una configuración para depurar el servidor.

## Alcance funcional

| Código | Función | Ruta |
|---|---|---|
| RF01 | Registrar incidentes y validar campos | `POST /tickets` |
| RF02 | Calcular prioridad = impacto (1–3) × urgencia (1–3) | `core.py` |
| RF03 | Consultar y filtrar reportes | `GET /` |
| RF04 | Consultar ficha de incidencia e historial | `GET /ticket/{id}` |
| RF05 | Cambiar estado con nota y flujo permitido | `POST /ticket/{id}/status` |
| RF06 | Visualizar indicadores generales | `GET /` |
| RF07 | Exportar reportes a CSV (para uso académico) | `GET /export.csv` |

**Reglas de prioridad**: Baja (puntajes 1 o 2), Media (3 o 4), Alta (6 o 9). No existen otros productos posibles al multiplicar 1, 2, 3.

**Flujo de estados:** Abierto → En proceso → Resuelto; se permite reabrir Resuelto → En proceso. Cada cambio necesita nota de 10 a 300 caracteres.

## Estructura

```
aulafix_repo/
├── app.py             # HTTP / rutas / controladores
├── core.py            # Validación y prioridad
├── db.py              # Repositorio SQLite / transacciones
├── views.py           # HTML escapado / componentes de pantalla
├── static/style.css   # Diseño responsive
├── demo_seed.py       # Datos falsos y opcionales
├── tests/
│   ├── test_aulafix.py    # Suite original (41 casos)
│   └── test_hardening.py  # Casos borde, seguridad local y robustez (68 casos)
├── .vscode/           # Configuración de pruebas y depuración
├── requirements-dev.txt  # Solo para medir cobertura (opcional)
├── LICENSE
├── .gitignore
└── README.md
```

## Consideraciones de seguridad y límites

- Se validan datos en servidor (longitudes, catálogos, saltos de línea normalizados y caracteres de control rechazados), se usan consultas SQL parametrizadas, el HTML dinámico se escapa y se envían encabezados de seguridad básicos. La exportación protege celdas que empiezan como fórmula.
- Como el servidor escucha en `127.0.0.1`, se valida el encabezado `Host` (frena *DNS rebinding*; una cabecera `Host` vacía o ausente también se rechaza) y, en los `POST`, `Origin` / `Sec-Fetch-Site` (frena que otra página web abierta en el navegador envíe formularios a AulaFix). Esta protección es **heurística, no un control de identidad por sesión**: un cliente que no sea navegador (script, `curl`) simplemente no envía esos encabezados y se acepta sin más verificación.
- El servidor responde siempre como HTTP/1.0 y cierra la conexión TCP después de cada petición (no hay *keep-alive*); cada solicitud abre una conexión nueva, tanto si la respuesta es exitosa como si es un error.
- Cualquier error inesperado responde una página HTTP 500 sin detalles internos; el detalle se imprime solo en la consola del servidor.
- **No incluye autenticación, autorización por roles ni HTTPS**. Tampoco registra qué usuario o actor realizó cada cambio de estado en la bitácora (la tabla `events` no tiene un campo de autor). Son limitaciones de alcance aceptadas para esta versión académica, no defectos pendientes. Es un prototipo local, **no apto para publicarse directamente en internet o utilizar datos personales reales**. Si se usa `--host` con una dirección distinta de loopback, el programa muestra una advertencia.
- No hay envío de correo ni integraciones institucionales; las alertas solo son visuales.
- Los indicadores son conteos, no mediciones de desempeño del soporte de una universidad real.

## Demostración sugerida (3 minutos)

1. Abrir el panel y revisar los indicadores; opcionalmente cargar casos ficticios.
2. Seleccionar «Registrar incidencia» y capturar un reporte con impacto 3, urgencia 2.
3. Abrir el reporte y confirmar que se asignó prioridad Alta (3 × 2 = 6).
4. Mover de Abierto a En proceso con una nota y comprobar que aparece en el historial.
5. Regresar al panel, filtrar por estado y exportar CSV.

## Pruebas automatizadas

El proyecto incluye una suite de 114 pruebas unitarias, de integración y de seguridad. Para ejecutarlas localmente:

```bash
python -m unittest discover -s tests -v