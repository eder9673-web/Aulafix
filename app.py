"""Servidor HTTP local sin librerías de terceros para AulaFix."""
import argparse
import csv
import io
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core import safe_csv_cell, validate_ticket
from db import Repository
from views import dashboard, message_page, new_ticket, ticket_details

BASE = Path(__file__).resolve().parent
MAX_FORM_BYTES = 12_000
REQUEST_TIMEOUT = 15  # segundos; evita hilos bloqueados por clientes lentos o incompletos
LOOPBACK_ADDRESSES = ("127.0.0.1", "::1")
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
# Solo dígitos ASCII y como máximo 19: así int() nunca recibe cadenas gigantes.
TICKET_PATH = re.compile(r"/ticket/([0-9]{1,19})")
STATUS_PATH = re.compile(r"/ticket/([0-9]{1,19})/status")
CSV_HEADER = ["Folio", "Título", "Categoría", "Ubicación", "Alias", "Impacto", "Urgencia",
              "Prioridad", "Estado", "Registro UTC"]
CSV_FIELDS = ("code", "title", "category", "location", "reporter", "impact", "urgency",
              "priority", "status", "created_at")


def build_csv(tickets) -> bytes:
    """CSV en UTF-8 con BOM (Excel) y celdas neutralizadas contra fórmulas."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_HEADER)
    for ticket in tickets:
        writer.writerow([safe_csv_cell(ticket[field]) for field in CSV_FIELDS])
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def handler_factory(repo):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AulaFix/1.0"
        timeout = REQUEST_TIMEOUT

        def log_message(self, format, *args):
            if os.environ.get("AULAFIX_QUIET") != "1":
                super().log_message(format, *args)

        # ---------- utilidades de respuesta ----------
        def respond(self, body, status=200, ctype="text/html; charset=utf-8", headers=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'")
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def error_page(self, status, title, message):
            self.respond(message_page(title, message, status), status)

        def redirect(self, location):
            self.respond(b"", status=303, headers={"Location": location})

        def path_parts(self):
            parsed = urlsplit(self.path)
            return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}

        # ---------- protección para uso local (CSRF y DNS rebinding) ----------
        def is_trusted_request(self, is_post):
            """Rechaza peticiones iniciadas por otras páginas web del navegador.

            - Host: si el servidor escucha solo en loopback, únicamente se aceptan
              127.0.0.1, localhost y [::1] con el puerto propio (frena DNS rebinding).
            - Origin / Sec-Fetch-Site (solo POST): los navegadores los envían siempre en
              envíos de formularios; si indican otro origen, es un CSRF. Los clientes que no
              son navegador (curl, urllib, pruebas) no los envían y siguen funcionando.
            """
            host = (self.headers.get("Host") or "").lower()
            bound = self.server.server_address[0]
            if bound in LOOPBACK_ADDRESSES:
                port = self.server.server_address[1]
                if not host or host not in {f"{name}:{port}" for name in LOOPBACK_NAMES}:
                    return False
            if is_post:
                origin = self.headers.get("Origin")
                if origin is not None:
                    try:
                        if urlsplit(origin).netloc.lower() != host:
                            return False
                    except ValueError:
                        return False
                site = self.headers.get("Sec-Fetch-Site")
                if site is not None and site not in ("same-origin", "none"):
                    return False
            return True

        def reject_untrusted(self):
            self.error_page(403, "Solicitud rechazada", "El origen o el host de la solicitud no están permitidos.")

        # ---------- manejo de errores no previstos ----------
        def guarded(self, action):
            """Ninguna excepción debe dejar al cliente sin respuesta."""
            try:
                action()
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception:
                if os.environ.get("AULAFIX_QUIET") != "1":
                    traceback.print_exc()
                try:
                    self.error_page(500, "Error interno", "Ocurrió un problema inesperado. Intenta de nuevo.")
                except Exception:
                    self.close_connection = True

        def read_form(self):
            raw_length = self.headers.get("Content-Length", "0").strip()
            # int() aceptaría "1_000" o signos; se exigen solo dígitos ASCII.
            length = int(raw_length) if raw_length.isascii() and raw_length.isdigit() else -1
            if length < 0:
                self.error_page(400, "Solicitud no válida", "La longitud del contenido no es válida.")
                return None
            if length > MAX_FORM_BYTES:
                self.error_page(413, "Solicitud no válida", "El formulario supera el tamaño permitido.")
                return None
            ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if ctype != "application/x-www-form-urlencoded":
                self.error_page(415, "Formato no aceptado", "Envía un formulario HTML estándar.")
                return None
            try:
                raw = self.rfile.read(length)
            except TimeoutError:
                self.close_connection = True
                self.error_page(408, "Tiempo agotado", "La solicitud tardó demasiado en recibirse.")
                return None
            if len(raw) < length:
                self.error_page(400, "Solicitud incompleta", "El formulario llegó incompleto.")
                return None
            try:
                parsed = parse_qs(raw.decode("utf-8", errors="strict"), keep_blank_values=True)
            except UnicodeDecodeError:
                self.error_page(422, "Texto inválido", "El formulario debe utilizar UTF-8.")
                return None
            return {k: values[0] for k, values in parsed.items()}

        # ---------- rutas ----------
        def do_GET(self):
            self.guarded(self.route_get)

        def do_POST(self):
            self.guarded(self.route_post)

        def route_get(self):
            if not self.is_trusted_request(is_post=False):
                return self.reject_untrusted()
            path, params = self.path_parts()
            if path == "/":
                q = params.get("q", "")[:90]
                status = params.get("status", "")
                self.respond(dashboard(repo.list_tickets(q, status), repo.metrics(), q, status))
            elif path == "/new":
                self.respond(new_ticket())
            elif path == "/static/style.css":
                self.respond((BASE / "static" / "style.css").read_bytes(), ctype="text/css; charset=utf-8")
            elif path == "/health":
                self.respond('{"status":"ok","app":"AulaFix"}', ctype="application/json; charset=utf-8")
            elif path == "/export.csv":
                self.respond(build_csv(repo.list_tickets()), ctype="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=incidencias_aulafix.csv"})
            else:
                match = TICKET_PATH.fullmatch(path)
                if match:
                    ticket_id = int(match.group(1))
                    ticket = repo.get_ticket(ticket_id)
                    if ticket:
                        return self.respond(ticket_details(ticket, repo.get_events(ticket_id)))
                self.error_page(404, "Página no encontrada", "La dirección o el folio no existen.")

        def route_post(self):
            if not self.is_trusted_request(is_post=True):
                return self.reject_untrusted()
            path, _ = self.path_parts()
            form = self.read_form()
            if form is None:
                return
            if path == "/tickets":
                cleaned, errors = validate_ticket(form)
                if errors:
                    return self.respond(new_ticket(form, errors), 422)
                ticket_id = repo.create_ticket(cleaned)
                return self.redirect(f"/ticket/{ticket_id}")
            match = STATUS_PATH.fullmatch(path)
            if match:
                ticket_id = int(match.group(1))
                try:
                    repo.change_status(ticket_id, form.get("status", ""), form.get("note", ""))
                except LookupError:
                    return self.error_page(404, "Folio inexistente", "No se encontró el registro.")
                except ValueError as error:
                    ticket = repo.get_ticket(ticket_id)
                    return self.respond(ticket_details(ticket, repo.get_events(ticket_id), [str(error)]), 409)
                return self.redirect(f"/ticket/{ticket_id}")
            self.error_page(404, "Ruta no encontrada", "No hay una acción para esta ruta.")

    return Handler


def create_server(database_path, host="127.0.0.1", port=8000):
    repo = Repository(database_path)
    return ThreadingHTTPServer((host, port), handler_factory(repo))


def main():
    parser = argparse.ArgumentParser(description="AulaFix - prototipo local de gestión de incidencias")
    parser.add_argument("--host", default="127.0.0.1", help="Solo uso local por defecto")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(BASE / "data" / "aulafix.db"))
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port debe estar entre 0 y 65535")
    if args.host not in LOOPBACK_ADDRESSES + ("localhost",):
        print("ADVERTENCIA: AulaFix no tiene autenticación ni HTTPS; "
              "exponerlo fuera de este equipo no es seguro.", file=sys.stderr, flush=True)
    try:
        server = create_server(args.db, args.host, args.port)
    except OSError as error:
        sys.exit(f"No se pudo iniciar en {args.host}:{args.port} ({error}). "
                 f"Si el puerto está ocupado, prueba con --port {args.port + 1}.")
    print(f"AulaFix disponible en http://{args.host}:{server.server_address[1]} (Ctrl+C para cerrar)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
