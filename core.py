"""Reglas de negocio y validaciones de AulaFix."""
import unicodedata
from datetime import datetime, timezone

CATEGORIES = ("Hardware", "Software", "Red e internet", "Periféricos", "Otro")
LOCATIONS = ("Laboratorio A", "Laboratorio B", "Biblioteca", "Aula multimedia")
STATES = ("Abierto", "En proceso", "Resuelto")
TRANSITIONS = {"Abierto": ("En proceso",), "En proceso": ("Resuelto",), "Resuelto": ("En proceso",)}


def normalize_text(value) -> str:
    """Recorta extremos y unifica los saltos de línea a '\\n'.

    Un navegador cuenta cada salto de línea como 1 carácter en `maxlength`,
    pero lo envía como '\\r\\n' (2 caracteres). Sin esta normalización, un texto
    válido para el navegador podía superar el límite en el servidor y ser
    rechazado sin una explicación clara.
    """
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def has_control_chars(text: str, allow_newlines: bool = False) -> bool:
    """Indica si el texto contiene caracteres de control Unicode (categoría Cc).

    SQLite calcula length() solo hasta el primer NUL; un texto con '\\x00'
    pasaba la validación de longitud en Python y luego violaba el CHECK de la
    base de datos (error interno). Se rechaza antes de llegar a ella.
    """
    allowed = "\n\t" if allow_newlines else ""
    return any(unicodedata.category(ch) == "Cc" and ch not in allowed for ch in text)


def priority(impact: int, urgency: int) -> tuple[str, int]:
    """Matriz académica explícita: impacto x urgencia, escala 1-3."""
    impact, urgency = int(impact), int(urgency)
    if impact not in (1, 2, 3) or urgency not in (1, 2, 3):
        raise ValueError("Impacto y urgencia deben estar entre 1 y 3.")
    score = impact * urgency
    if score >= 6:
        return ("Alta", score)
    if score >= 3:
        return ("Media", score)
    return ("Baja", score)


def validate_ticket(data: dict) -> tuple[dict, list[str]]:
    title = normalize_text(data.get("title"))
    description = normalize_text(data.get("description"))
    location = normalize_text(data.get("location"))
    category = normalize_text(data.get("category"))
    reporter = normalize_text(data.get("reporter"))
    errors = []
    if not 8 <= len(title) <= 100:
        errors.append("El título debe tener entre 8 y 100 caracteres.")
    if not 20 <= len(description) <= 700:
        errors.append("La descripción debe tener entre 20 y 700 caracteres.")
    if category not in CATEGORIES:
        errors.append("Selecciona una categoría válida.")
    if location not in LOCATIONS:
        errors.append("Selecciona una ubicación válida.")
    if not 2 <= len(reporter) <= 40:
        errors.append("El identificador debe tener entre 2 y 40 caracteres (evita datos sensibles).")
    for label, text, multiline in (("El título", title, False),
                                   ("La descripción", description, True),
                                   ("El identificador", reporter, False)):
        if has_control_chars(text, allow_newlines=multiline):
            errors.append(f"{label} contiene caracteres no permitidos.")
    try:
        impact = int(data.get("impact", ""))
        urgency = int(data.get("urgency", ""))
    except (ValueError, TypeError):
        impact, urgency = 0, 0
    if impact not in (1, 2, 3) or urgency not in (1, 2, 3):
        errors.append("Impacto y urgencia deben tener valores entre 1 y 3.")
    normalized = dict(title=title, description=description, category=category,
                      location=location, reporter=reporter, impact=impact, urgency=urgency)
    return normalized, errors


def validate_transition(previous: str, next_state: str, note: str) -> list[str]:
    errors = []
    if next_state not in TRANSITIONS.get(previous, ()):
        errors.append("Ese cambio de estado no está permitido en el flujo definido.")
    clean_note = normalize_text(note)
    if not 10 <= len(clean_note) <= 300:
        errors.append("La nota debe tener entre 10 y 300 caracteres.")
    if has_control_chars(clean_note, allow_newlines=True):
        errors.append("La nota contiene caracteres no permitidos.")
    return errors


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def safe_csv_cell(value) -> str:
    """Evita que programas de hoja de cálculo interpreten texto como fórmula."""
    s = str(value)
    if s.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + s
    return s
