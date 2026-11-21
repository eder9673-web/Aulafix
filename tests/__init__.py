"""Paquete de pruebas de AulaFix.

La variable silencia el registro de accesos del servidor durante las pruebas
(los tracebacks de errores inesperados también se omiten solo en este modo).
"""
import os

os.environ.setdefault("AULAFIX_QUIET", "1")
