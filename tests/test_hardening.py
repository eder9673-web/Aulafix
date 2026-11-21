"""Pruebas de casos borde, seguridad local y robustez (complementan test_aulafix.py).

Cada grupo corresponde a un defecto o hueco detectado en la auditoría.
Todas usan bases SQLite temporales y datos ficticios.
"""
import csv
import http.client
import io
import os
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

os.environ.setdefault("AULAFIX_QUIET", "1")  # silencia el log de accesos (se lee en cada petición)

import app
from app import build_csv, create_server
from core import (TRANSITIONS, has_control_chars, normalize_text, priority,
                  safe_csv_cell, validate_ticket, validate_transition)
from db import SQLITE_MAX_INT, Repository, like_pattern

GOOD = dict(title="Sin acceso a la red del laboratorio",
            description="Las computadoras del aula no pueden conectarse a la plataforma de clases.",
            category="Red e internet", location="Laboratorio A", reporter="estudiante-07",
            impact="3", urgency="2")


def sample(**overrides):
    return {**GOOD, **overrides}


# --------------------------------------------------------------------------- reglas de negocio
class ValidationBoundaries(unittest.TestCase):
    """Valores límite exactos (n-1, n, máx, máx+1) de cada campo con longitud."""

    def assert_bounds(self, field, low, high, build=lambda n: "a" * n):
        for length, should_fail in ((low - 1, True), (low, False), (high, False), (high + 1, True)):
            with self.subTest(field=field, length=length):
                _, errors = validate_ticket(sample(**{field: build(length)}))
                self.assertEqual(bool(errors), should_fail, errors)

    def test_title_bounds(self):
        self.assert_bounds("title", 8, 100)

    def test_description_bounds(self):
        self.assert_bounds("description", 20, 700)

    def test_reporter_bounds(self):
        self.assert_bounds("reporter", 2, 40)

    def test_note_bounds(self):
        for length, should_fail in ((9, True), (10, False), (300, False), (301, True)):
            with self.subTest(length=length):
                errors = validate_transition("Abierto", "En proceso", "a" * length)
                self.assertEqual(bool(errors), should_fail, errors)

    def test_whitespace_only_fields_are_rejected(self):
        for field in ("title", "description", "reporter"):
            with self.subTest(field=field):
                self.assertTrue(validate_ticket(sample(**{field: " " * 50}))[1])

    def test_missing_fields_are_rejected_not_crash(self):
        clean, errors = validate_ticket({})
        self.assertGreaterEqual(len(errors), 5)
        clean, errors = validate_ticket({"title": None, "description": None})
        self.assertTrue(errors)

    def test_priority_matrix_is_exhaustive(self):
        expected = {1: "Baja", 2: "Baja", 3: "Media", 4: "Media", 6: "Alta", 9: "Alta"}
        for impact in (1, 2, 3):
            for urgency in (1, 2, 3):
                with self.subTest(impact=impact, urgency=urgency):
                    label, score = priority(impact, urgency)
                    self.assertEqual(score, impact * urgency)
                    self.assertEqual(label, expected[score])

    def test_priority_rejects_values_outside_scale(self):
        for impact, urgency in ((0, 1), (1, 0), (4, 1), (1, 4), (-1, 2)):
            with self.subTest(impact=impact, urgency=urgency):
                with self.assertRaises(ValueError):
                    priority(impact, urgency)

    def test_state_machine_exhaustive(self):
        allowed = {("Abierto", "En proceso"), ("En proceso", "Resuelto"), ("Resuelto", "En proceso")}
        states = ("Abierto", "En proceso", "Resuelto")
        for old in states:
            for new in states:
                with self.subTest(old=old, new=new):
                    errors = validate_transition(old, new, "Nota de seguimiento válida")
                    self.assertEqual(not errors, (old, new) in allowed, errors)
        self.assertEqual({(o, n) for o, ns in TRANSITIONS.items() for n in ns}, allowed)

    def test_unknown_states_are_rejected(self):
        self.assertTrue(validate_transition("Cerrado", "En proceso", "Nota de seguimiento válida"))
        self.assertTrue(validate_transition("Abierto", "Inventado", "Nota de seguimiento válida"))


class TextNormalization(unittest.TestCase):
    """Defecto: el navegador cuenta '\\n' como 1 carácter pero envía '\\r\\n' (2)."""

    def test_normalize_text(self):
        self.assertEqual(normalize_text("  a\r\nb\rc\nd  "), "a\nb\nc\nd")
        self.assertEqual(normalize_text(None), "")

    def test_crlf_text_valid_in_browser_is_accepted_by_server(self):
        browser_value = ("x" * 9 + "\n") * 70            # 700 caracteres según el navegador
        wire_value = browser_value.replace("\n", "\r\n")  # 770 en el servidor sin normalizar
        self.assertEqual(len(browser_value), 700)
        clean, errors = validate_ticket(sample(description=wire_value))
        self.assertEqual(errors, [])
        self.assertNotIn("\r", clean["description"])

    def test_limit_still_enforced_after_normalization(self):
        _, errors = validate_ticket(sample(description="x" * 701))
        self.assertTrue(errors)

    def test_control_characters_rejected(self):
        for field in ("title", "description", "reporter"):
            with self.subTest(field=field):
                text = "abc\x00defghijklmnopqrstuvwxyz"
                self.assertTrue(any("no permitidos" in e for e in validate_ticket(sample(**{field: text}))[1]))
        self.assertTrue(any("no permitidos" in e for e in validate_transition("Abierto", "En proceso", "Nota\x00 con NUL ok")))

    def test_multiline_fields_allow_newline_and_tab_but_single_line_do_not(self):
        self.assertFalse(has_control_chars("línea 1\n\tlínea 2", allow_newlines=True))
        self.assertTrue(has_control_chars("línea 1\nlínea 2"))
        _, errors = validate_ticket(sample(title="Título con\nsalto de línea"))
        self.assertTrue(errors)

    def test_unicode_text_is_accepted(self):
        clean, errors = validate_ticket(sample(title="Teclado sin respuesta — ñandú 🖥️", reporter="téc-ñ"))
        self.assertEqual(errors, [])


class CsvSanitization(unittest.TestCase):
    def test_formula_prefixes_are_neutralized(self):
        for value in ("=1+1", "+1", "-5 equipos", "@SUM(A1)", " =1+1", "\t=1+1", "\r=1+1", "=HYPERLINK(\"x\")"):
            with self.subTest(value=value):
                self.assertTrue(safe_csv_cell(value).startswith("'"))

    def test_regular_values_are_unchanged(self):
        for value in ("Laboratorio A", "3", "a=b", "correo@ejemplo.mx", "5-3", "Red e internet"):
            with self.subTest(value=value):
                self.assertEqual(safe_csv_cell(value), value)

    def test_build_csv_roundtrip_with_commas_and_quotes(self):
        ticket = dict(code="INC-2026-0001", title='Falla "grave", en red', category="Hardware",
                      location="Biblioteca", reporter="+cmd", impact=3, urgency=2, priority="Alta",
                      status="Abierto", created_at="2026-11-20 10:00 UTC")
        raw = build_csv([ticket])
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        self.assertEqual(rows[1][1], 'Falla "grave", en red')
        self.assertEqual(rows[1][4], "'+cmd")
        self.assertEqual(len(rows[0]), len(rows[1]))


# --------------------------------------------------------------------------- SQLite
class RepositoryEdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.tmp.name) / "edge.db")

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **kwargs):
        clean, errors = validate_ticket(sample(**kwargs))
        self.assertEqual(errors, [])
        return self.repo.create_ticket(clean)

    def test_like_wildcards_are_literal(self):
        self.create(title="Falla con 100% de pérdida")
        self.create(title="Falla_con_guion_bajo_en_red")
        self.create(title="Falla ordinaria de la red")
        self.assertEqual([t["title"] for t in self.repo.list_tickets("%")], ["Falla con 100% de pérdida"])
        self.assertEqual([t["title"] for t in self.repo.list_tickets("_")], ["Falla_con_guion_bajo_en_red"])
        self.assertEqual(self.repo.list_tickets("\\"), [])
        self.assertEqual(len(self.repo.list_tickets("100%")), 1)

    def test_like_pattern_escaping(self):
        self.assertEqual(like_pattern("a%b_c\\d"), "%a\\%b\\_c\\\\d%")

    def test_search_and_status_filters_combine(self):
        first = self.create(title="Falla de red en laboratorio")
        self.create(title="Falla de red en biblioteca")
        self.repo.change_status(first, "En proceso", "Se inició la revisión de la red")
        self.assertEqual(len(self.repo.list_tickets("red", "En proceso")), 1)
        self.assertEqual(len(self.repo.list_tickets("red", "Abierto")), 1)
        self.assertEqual(len(self.repo.list_tickets("red", "Resuelto")), 0)

    def test_out_of_range_ids_do_not_raise(self):
        for bad_id in (0, -1, SQLITE_MAX_INT + 1, 10**30):
            with self.subTest(ticket_id=bad_id):
                self.assertIsNone(self.repo.get_ticket(bad_id))
                self.assertEqual(self.repo.get_events(bad_id), [])
                with self.assertRaises(LookupError):
                    self.repo.change_status(bad_id, "En proceso", "Nota de seguimiento válida")

    def test_missing_ticket_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            self.repo.change_status(12345, "En proceso", "Nota de seguimiento válida")

    def test_note_is_stored_normalized(self):
        ticket_id = self.create()
        self.repo.change_status(ticket_id, "En proceso", "  Se revisó\r\nel equipo del laboratorio  ")
        note = self.repo.get_events(ticket_id)[0]["note"]
        self.assertEqual(note, "Se revisó\nel equipo del laboratorio")

    def test_database_rejects_values_that_bypass_validation(self):
        """El CHECK de SQLite es la última defensa si alguien salta core.validate_ticket."""
        import sqlite3
        clean, _ = validate_ticket(sample())
        clean["title"] = "corto"
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.create_ticket(clean)

    def test_concurrent_status_change_has_exactly_one_winner(self):
        ticket_id = self.create()
        workers = 8
        barrier = threading.Barrier(workers)
        results = []

        def attempt():
            barrier.wait()
            try:
                self.repo.change_status(ticket_id, "En proceso", "Intento concurrente de cambio")
                results.append("ok")
            except ValueError:
                results.append("rechazado")

        threads = [threading.Thread(target=attempt) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["ok"] + ["rechazado"] * (workers - 1))
        self.assertEqual(len(self.repo.get_events(ticket_id)), 2)  # creación + un solo cambio

    def test_concurrent_creates_produce_unique_codes(self):
        clean, _ = validate_ticket(sample())
        ids = []
        lock = threading.Lock()

        def create():
            ticket_id = self.repo.create_ticket(clean)
            with lock:
                ids.append(ticket_id)

        threads = [threading.Thread(target=create) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        codes = [self.repo.get_ticket(i)["code"] for i in ids]
        self.assertEqual(len(set(codes)), 20)


# --------------------------------------------------------------------------- HTTP
class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "http.db"
        self.server = create_server(self.db_path, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.repo = Repository(self.db_path)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def http(self, method, path, form=None, headers=None, body=None):
        """Devuelve (status, texto, cabeceras en minúsculas). No sigue redirecciones."""
        hdrs = dict(headers or {})
        if form is not None:
            body = urlencode(form).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", "replace")
            return resp.status, text, {k.lower(): v for k, v in resp.getheaders()}
        finally:
            conn.close()

    def raw(self, payload: bytes, shutdown_write=False, read_timeout=3):
        """Envía bytes crudos y devuelve la línea de estado de la respuesta."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=read_timeout) as sock:
            sock.sendall(payload)
            if shutdown_write:
                sock.shutdown(socket.SHUT_WR)
            return sock.recv(300).split(b"\r\n")[0].decode("latin-1")

    def head(self, extra=""):
        return (f"POST /tickets HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                f"Content-Type: application/x-www-form-urlencoded\r\n{extra}").encode()

    def create(self, **kwargs):
        status, _, headers = self.http("POST", "/tickets", sample(**kwargs))
        self.assertEqual(status, 303)
        return headers["location"]

    def ticket_count(self):
        return self.repo.metrics()["total"]


class RouteRobustness(ServerCase):
    """Defecto: excepciones no controladas cortaban la conexión sin responder."""

    def test_huge_or_malformed_ticket_ids_return_404(self):
        for path in ("/ticket/99999999999999999999", "/ticket/" + "9" * 5000, "/ticket/0",
                     "/ticket/9223372036854775808", "/ticket/-1", "/ticket/1.5", "/ticket/%D9%A3", "/ticket/"):
            with self.subTest(path=path[:40]):
                self.assertEqual(self.http("GET", path)[0], 404)

    def test_huge_id_on_status_post_returns_404(self):
        status, _, _ = self.http("POST", "/ticket/99999999999999999999/status",
                                 {"status": "En proceso", "note": "Nota de seguimiento válida"})
        self.assertEqual(status, 404)

    def test_unexpected_exception_returns_500_and_server_survives(self):
        with mock.patch.object(Repository, "metrics", side_effect=RuntimeError("boom")):
            with redirect_stderr(io.StringIO()):
                status, body, headers = self.http("GET", "/")
        self.assertEqual(status, 500)
        self.assertIn("Error interno", body)
        self.assertNotIn("boom", body)                      # no se filtran detalles internos
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.http("GET", "/health")[0], 200)

    def test_missing_static_file_returns_500_not_dropped_connection(self):
        with mock.patch.object(app, "BASE", Path(self.tmp.name)):
            status, _, _ = self.http("GET", "/static/style.css")
        self.assertEqual(status, 500)

    def test_path_traversal_on_static_is_not_served(self):
        for path in ("/static/../app.py", "/static/style.css/..", "/static/", "/static/%2e%2e/app.py"):
            with self.subTest(path=path):
                self.assertEqual(self.http("GET", path)[0], 404)

    def test_unknown_routes_and_methods_on_known_paths(self):
        self.assertEqual(self.http("GET", "/tickets")[0], 404)
        self.assertEqual(self.http("GET", "/ticket/1/status")[0], 404)
        self.assertEqual(self.http("GET", "/ticket/1/")[0], 404)
        self.assertEqual(self.http("POST", "/", {"a": "b"})[0], 404)


class FormParsing(ServerCase):
    def test_invalid_content_length_values_return_400(self):
        for value in ("abc", "-5", "1_000", "+10", "1.5", ""):
            with self.subTest(value=value):
                line = self.raw(self.head(f"Content-Length: {value}\r\n\r\n"))
                self.assertIn("400", line)

    def test_oversized_body_returns_413(self):
        status, _, _ = self.http("POST", "/tickets", sample(description="x" * 60000))
        self.assertEqual(status, 413)

    def test_wrong_content_type_returns_415(self):
        status, _, _ = self.http("POST", "/tickets", body=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 415)

    def test_invalid_utf8_returns_422(self):
        status, _, _ = self.http("POST", "/tickets", body=b"title=%ff%fe",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 422)

    def test_truncated_body_returns_400(self):
        line = self.raw(self.head("Content-Length: 500\r\n\r\nabc"), shutdown_write=True)
        self.assertIn("400", line)
        self.assertEqual(self.ticket_count(), 0)

    def test_worst_case_valid_form_fits_in_size_limit(self):
        """Texto máximo con caracteres de 4 bytes no debe chocar con el límite de 12 000 bytes."""
        status, _, _ = self.http("POST", "/tickets", sample(
            title="🖥" * 100, description="🖥" * 700, reporter="🖥" * 40))
        self.assertEqual(status, 303)

    def test_nul_byte_in_title_is_rejected_with_422_not_a_crash(self):
        status, body, _ = self.http("POST", "/tickets", sample(title="abc\x00defghijkl"))
        self.assertEqual(status, 422)
        self.assertIn("no permitidos", body)
        self.assertEqual(self.ticket_count(), 0)

    def test_crlf_description_from_browser_is_accepted(self):
        browser_value = ("x" * 9 + "\n") * 70
        status, _, headers = self.http("POST", "/tickets", sample(description=browser_value.replace("\n", "\r\n")))
        self.assertEqual(status, 303)
        stored = self.repo.get_ticket(int(headers["location"].rsplit("/", 1)[1]))["description"]
        self.assertNotIn("\r", stored)

    def test_form_with_duplicate_keys_uses_first_value_and_extra_keys_are_ignored(self):
        body = urlencode(sample()) + "&title=otro+titulo+distinto&admin=1"
        status, _, _ = self.http("POST", "/tickets", body=body.encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 303)
        self.assertEqual(self.repo.list_tickets()[0]["title"], GOOD["title"])


class ConnectionHandling(ServerCase):
    """Caracteriza que el servidor NUNCA mantiene conexiones persistentes.

    Handler no fija protocol_version a 'HTTP/1.1', así que http.server deja
    close_connection=True sin importar la cabecera Connection del cliente.
    Cada conexión TCP atiende una sola petición, tanto en el camino exitoso
    como en el de error; por eso un cuerpo no leído en un error no puede
    desincronizar una conexión persistente (no existe tal persistencia).
    """

    def _full_response(self, sock):
        resp = http.client.HTTPResponse(sock)
        resp.begin()
        resp.read()
        return resp

    def test_connection_closes_after_successful_response(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
            resp = self._full_response(sock)
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.will_close)

            sock.settimeout(2)
            try:
                sock.sendall(f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
                second = sock.recv(4096)
            except (ConnectionResetError, BrokenPipeError, OSError):
                second = b""
            self.assertEqual(second, b"",
                             "El servidor no debe responder una segunda peticion en la misma conexion")

    def test_connection_closes_after_error_response_too(self):
        body = "a" * 60000
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(self.head(f"Content-Length: {len(body)}\r\n\r\n{body}"))
            resp = self._full_response(sock)
            self.assertEqual(resp.status, 413)
            self.assertTrue(resp.will_close)

            sock.settimeout(2)
            try:
                sock.sendall(f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
                second = sock.recv(4096)
            except (ConnectionResetError, BrokenPipeError, OSError):
                second = b""
            self.assertEqual(second, b"",
                             "El servidor no debe responder una segunda peticion en la misma conexion")


class SlowClient(ServerCase):
    """Defecto: sin timeout, un cuerpo incompleto dejaba un hilo bloqueado indefinidamente."""

    def setUp(self):
        patcher = mock.patch.object(app, "REQUEST_TIMEOUT", 0.3)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_incomplete_body_gets_408_after_timeout(self):
        line = self.raw(self.head("Content-Length: 500\r\n\r\nabc"), read_timeout=3)
        self.assertIn("408", line)


class CrossSiteProtection(ServerCase):
    """Defecto: cualquier página web podía enviar formularios a http://127.0.0.1:8000 (CSRF)."""

    def test_cross_origin_post_is_rejected_and_has_no_effect(self):
        status, _, _ = self.http("POST", "/tickets", sample(), {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.ticket_count(), 0)

    def test_null_and_malformed_origin_are_rejected(self):
        for origin in ("null", "http://[", "", "http://127.0.0.1:1"):
            with self.subTest(origin=origin):
                self.assertEqual(self.http("POST", "/tickets", sample(), {"Origin": origin})[0], 403)
        self.assertEqual(self.ticket_count(), 0)

    def test_same_origin_post_is_accepted(self):
        status, _, _ = self.http("POST", "/tickets", sample(), {"Origin": self.origin})
        self.assertEqual(status, 303)

    def test_sec_fetch_site_values(self):
        for value, expected in (("same-origin", 303), ("none", 303), ("cross-site", 403), ("same-site", 403)):
            with self.subTest(value=value):
                self.assertEqual(self.http("POST", "/tickets", sample(), {"Sec-Fetch-Site": value})[0], expected)

    def test_non_browser_clients_without_origin_headers_still_work(self):
        self.assertEqual(self.http("POST", "/tickets", sample())[0], 303)

    def test_cross_origin_status_change_is_rejected(self):
        self.create()
        status, _, _ = self.http("POST", "/ticket/1/status", {"status": "En proceso", "note": "Nota de seguimiento válida"},
                                 {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.repo.get_ticket(1)["status"], "Abierto")

    def test_missing_host_header_is_rejected_on_loopback(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"GET / HTTP/1.1\r\n\r\n")
            resp = http.client.HTTPResponse(sock)
            resp.begin()
            self.assertEqual(resp.status, 403)

    def test_host_header_allowlist_blocks_dns_rebinding(self):
        for host, expected in ((f"127.0.0.1:{self.port}", 200), (f"localhost:{self.port}", 200),
                               (f"LOCALHOST:{self.port}", 200), (f"[::1]:{self.port}", 200),
                               ("attacker.example", 403), (f"attacker.example:{self.port}", 403),
                               ("127.0.0.1:1", 403)):
            with self.subTest(host=host):
                self.assertEqual(self.http("GET", "/", headers={"Host": host})[0], expected)

    def test_rebinding_cannot_read_csv_export(self):
        self.create()
        status, body, _ = self.http("GET", "/export.csv", headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)
        self.assertNotIn(GOOD["title"], body)


class XssAndOutputEncoding(ServerCase):
    def test_stored_xss_is_escaped_in_detail_page(self):
        location = self.create(title="<b>negrita</b> falla de equipo",
                               description="<img src=x onerror=alert(1)> descripción de relleno",
                               reporter="<i>alias</i>")
        _, body, _ = self.http("GET", location)
        for raw_fragment in ("<b>negrita", "<img", "<i>alias"):
            self.assertNotIn(raw_fragment, body)
        self.assertIn("&lt;b&gt;negrita", body)

    def test_note_is_escaped_in_timeline(self):
        location = self.create()
        self.http("POST", location + "/status", {"status": "En proceso", "note": "<script>alert(2)</script> revisión"})
        _, body, _ = self.http("GET", location)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;alert(2)", body)

    def test_attribute_injection_in_rerendered_form_is_escaped(self):
        payload = '" autofocus onfocus="alert(1)'
        status, body, _ = self.http("POST", "/tickets", sample(title=payload, description="corto"))
        self.assertEqual(status, 422)
        self.assertNotIn('value="" autofocus', body)
        self.assertIn("&quot; autofocus", body)

    def test_search_term_is_escaped_in_search_box(self):
        _, body, _ = self.http("GET", "/?q=%22%3E%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertNotIn("<script>alert(1)", body)

    def test_unknown_status_filter_is_not_reflected(self):
        _, body, _ = self.http("GET", "/?status=%3Cscript%3E")
        self.assertNotIn("<script>", body)

    def test_every_response_carries_security_headers(self):
        for method, path in (("GET", "/"), ("GET", "/new"), ("GET", "/nada"), ("GET", "/export.csv"),
                             ("GET", "/static/style.css"), ("GET", "/health")):
            with self.subTest(path=path):
                _, _, headers = self.http(method, path)
                self.assertEqual(headers["x-content-type-options"], "nosniff")
                self.assertEqual(headers["x-frame-options"], "DENY")
                self.assertIn("default-src 'self'", headers["content-security-policy"])
                self.assertEqual(headers["cache-control"], "no-store")


class EndToEndFlow(ServerCase):
    def test_full_lifecycle_including_reopen(self):
        location = self.create()
        self.assertEqual(location, "/ticket/1")
        steps = (("En proceso", "Se inició el diagnóstico del equipo"),
                 ("Resuelto", "Se reemplazó el cable y se confirmó la conexión"),
                 ("En proceso", "La falla reapareció durante la práctica de hoy"))
        for state, note in steps:
            status, _, _ = self.http("POST", location + "/status", {"status": state, "note": note})
            self.assertEqual(status, 303)
        ticket = self.repo.get_ticket(1)
        self.assertEqual(ticket["status"], "En proceso")
        events = self.repo.get_events(1)
        self.assertEqual([e["new_status"] for e in events], ["En proceso", "Resuelto", "En proceso", "Abierto"])
        self.assertEqual([e["old_status"] for e in events], ["Resuelto", "En proceso", "Abierto", None])
        _, body, _ = self.http("GET", "/")
        self.assertIn("En proceso", body)

    def test_rejected_transition_leaves_no_trace(self):
        location = self.create()
        status, body, _ = self.http("POST", location + "/status",
                                    {"status": "Resuelto", "note": "Intento de salto no permitido"})
        self.assertEqual(status, 409)
        self.assertIn("no está permitido", body)
        self.assertEqual(self.repo.get_ticket(1)["status"], "Abierto")
        self.assertEqual(len(self.repo.get_events(1)), 1)

    def test_short_note_is_rejected_with_409(self):
        location = self.create()
        self.assertEqual(self.http("POST", location + "/status", {"status": "En proceso", "note": "ok"})[0], 409)

    def test_status_post_for_missing_ticket_returns_404(self):
        status, _, _ = self.http("POST", "/ticket/777/status", {"status": "En proceso", "note": "Nota de seguimiento válida"})
        self.assertEqual(status, 404)

    def test_csv_export_matches_database_and_neutralizes_formulas(self):
        self.create(title='Falla "grave", en red', reporter="+cmd|calc")
        self.create(title="-5 equipos sin red", reporter="@alias")
        status, body, headers = self.http("GET", "/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        rows = list(csv.reader(io.StringIO(body.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 3)
        titles = {r[1] for r in rows[1:]}
        self.assertEqual(titles, {'Falla "grave", en red', "'-5 equipos sin red"})
        self.assertTrue(all(r[4].startswith("'") for r in rows[1:]))

    def test_dashboard_counters_follow_state_changes(self):
        for _ in range(3):
            self.create()
        self.http("POST", "/ticket/1/status", {"status": "En proceso", "note": "Se inició el diagnóstico del equipo"})
        metrics = self.repo.metrics()
        self.assertEqual((metrics["total"], metrics["open"], metrics["progress"], metrics["resolved"]), (3, 2, 1, 0))
        self.assertEqual(metrics["high_pending"], 3)  # impacto 3 x urgencia 2 = Alta; sigue pendiente


# --------------------------------------------------------------------------- arranque (main)
class StartupBehavior(unittest.TestCase):
    def run_main(self, argv, create_server_mock=None):
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["app.py", *argv]), redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            if create_server_mock is not None:
                with mock.patch.object(app, "create_server", create_server_mock):
                    app.main()
            else:
                app.main()
        return stderr.getvalue()

    def test_invalid_port_exits_with_usage_error(self):
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            with mock.patch.object(sys, "argv", ["app.py", "--port", "70000"]):
                app.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_bind_failure_gives_friendly_message_instead_of_traceback(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_main(["--port", "8000"], mock.Mock(side_effect=OSError("Address already in use")))
        self.assertIn("--port 8001", str(ctx.exception.code))

    def test_non_loopback_host_prints_warning(self):
        class FakeServer:
            server_address = ("0.0.0.0", 8000)
            def serve_forever(self): raise KeyboardInterrupt
            def server_close(self): pass
        out = self.run_main(["--host", "0.0.0.0"], mock.Mock(return_value=FakeServer()))
        self.assertIn("ADVERTENCIA", out)

    def test_loopback_host_prints_no_warning(self):
        class FakeServer:
            server_address = ("127.0.0.1", 8000)
            def serve_forever(self): raise KeyboardInterrupt
            def server_close(self): pass
        out = self.run_main([], mock.Mock(return_value=FakeServer()))
        self.assertNotIn("ADVERTENCIA", out)


if __name__ == "__main__":
    unittest.main()
