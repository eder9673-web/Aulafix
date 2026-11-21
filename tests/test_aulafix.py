"""Pruebas unitarias e integradas, sobre datos aislados y ficticios."""
import csv
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app import create_server
from core import priority, safe_csv_cell, validate_ticket, validate_transition
from db import Repository


def sample(**overrides):
    d=dict(title="Sin acceso a la red del laboratorio",
           description="Las computadoras del aula no pueden conectarse a la plataforma de clases.",
           category="Red e internet",location="Laboratorio A",reporter="estudiante-07",impact="3",urgency="2")
    d.update(overrides)
    return d


class BusinessRules(unittest.TestCase):
    def test_priority_low(self): self.assertEqual(priority(1,2),("Baja",2))
    def test_priority_medium(self): self.assertEqual(priority(2,2),("Media",4))
    def test_priority_high(self): self.assertEqual(priority(3,3),("Alta",9))
    def test_priority_high_with_score_six(self): self.assertEqual(priority(2,3),("Alta",6))
    def test_validation_accepts_complete_ticket(self): self.assertEqual(validate_ticket(sample())[1],[])
    def test_title_too_short_rejected(self): self.assertTrue(validate_ticket(sample(title="Error"))[1])
    def test_description_too_short_rejected(self): self.assertTrue(validate_ticket(sample(description="Se dañó"))[1])
    def test_invalid_category_rejected(self): self.assertTrue(validate_ticket(sample(category="No válida"))[1])
    def test_invalid_location_rejected(self): self.assertTrue(validate_ticket(sample(location="Laboratorio X"))[1])
    def test_missing_alias_rejected(self): self.assertTrue(validate_ticket(sample(reporter=""))[1])
    def test_invalid_impact_rejected(self): self.assertTrue(validate_ticket(sample(impact="99"))[1])
    def test_invalid_urgency_rejected(self): self.assertTrue(validate_ticket(sample(urgency="-1"))[1])
    def test_invalid_numeric_text_rejected(self): self.assertTrue(validate_ticket(sample(impact="alto"))[1])
    def test_transition_open_to_process(self): self.assertEqual(validate_transition("Abierto","En proceso","Se revisa el equipo"),[])
    def test_transition_cannot_skip_stage(self): self.assertTrue(validate_transition("Abierto","Resuelto","Se resolvió todo")[0])
    def test_transition_note_required(self): self.assertTrue(validate_transition("Abierto","En proceso","ok")[0])
    def test_csv_formula_escaped(self): self.assertEqual(safe_csv_cell("=2+2"),"'=2+2")
    def test_csv_regular_value(self): self.assertEqual(safe_csv_cell("Laboratorio A"),"Laboratorio A")


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.repo=Repository(Path(self.tmp.name)/"test.db")
    def tearDown(self): self.tmp.cleanup()
    def create(self,**kwargs):
        obj,errors=validate_ticket(sample(**kwargs));self.assertFalse(errors)
        return self.repo.create_ticket(obj)
    def test_create_generates_unique_codes(self):
        one=self.create();two=self.create()
        self.assertNotEqual(self.repo.get_ticket(one)['code'],self.repo.get_ticket(two)['code'])
    def test_insertion_creates_audit_event(self):
        ticket_id=self.create()
        self.assertEqual(len(self.repo.get_events(ticket_id)),1)
    def test_state_change_persists_with_event(self):
        ticket_id=self.create()
        self.repo.change_status(ticket_id,"En proceso","Se inició la revisión del equipo")
        self.assertEqual(self.repo.get_ticket(ticket_id)['status'],"En proceso")
        self.assertEqual(len(self.repo.get_events(ticket_id)),2)
    def test_invalid_change_rolls_back(self):
        ticket_id=self.create()
        with self.assertRaises(ValueError):
            self.repo.change_status(ticket_id,"Resuelto","Intento de salto no permitido")
        self.assertEqual(self.repo.get_ticket(ticket_id)['status'],"Abierto")
        self.assertEqual(len(self.repo.get_events(ticket_id)),1)
    def test_reopen_ticket_is_allowed(self):
        ticket_id=self.create()
        self.repo.change_status(ticket_id,"En proceso","El técnico inició diagnóstico")
        self.repo.change_status(ticket_id,"Resuelto","Se confirmó que el equipo funciona")
        self.repo.change_status(ticket_id,"En proceso","La falla reapareció durante la práctica")
        self.assertEqual(self.repo.get_ticket(ticket_id)['status'],"En proceso")
        self.assertEqual(len(self.repo.get_events(ticket_id)),4)
    def test_foreign_key_blocks_orphan_event(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repo.connection() as db:
                db.execute("INSERT INTO events(ticket_id,new_status,note,created_at) VALUES (999,'Abierto','test','hoy')")
    def test_metrics_are_consistent(self):
        tid=self.create(); self.create()
        self.repo.change_status(tid,"En proceso","Se está validando conexión a la red")
        m=self.repo.metrics()
        self.assertEqual((m['total'],m['open'],m['progress']), (2,1,1))
    def test_parametrized_search_handles_sql_like_payload(self):
        self.create();self.assertEqual(len(self.repo.list_tickets("' OR 1=1 --")),0)


class HTTPIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.server=create_server(Path(self.tmp.name)/"http.db",port=0)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":0.05},daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.tmp.cleanup()
    def request(self,path,form=None,ctype=None):
        data=urlencode(form).encode('utf-8') if form is not None else None
        headers={"Content-Type":ctype or "application/x-www-form-urlencoded"} if data is not None else {}
        request=Request(f"http://127.0.0.1:{self.port}{path}",data=data,headers=headers)
        try:
            with urlopen(request,timeout=3) as resp:
                return resp.status, resp.read(), resp.headers
        except HTTPError as exc:
            with exc:
                return exc.code,exc.read(),exc.headers
    def create(self,**kwargs):
        r=self.request('/tickets',sample(**kwargs))
        self.assertEqual(r[0],200)  # urllib sigue automáticamente 303 al detalle
        return r
    def test_health_endpoint(self):
        code,data,_=self.request('/health')
        self.assertEqual(code,200);self.assertEqual(json.loads(data)['status'],'ok')
    def test_dashboard_empty_state(self):
        code,data,_=self.request('/')
        self.assertEqual(code,200);self.assertIn('Aún no hay incidencias',data.decode('utf-8'))
    def test_registration_page_loads(self):
        code,data,_=self.request('/new')
        self.assertEqual(code,200);self.assertIn('Registrar una incidencia',data.decode('utf-8'))
    def test_can_create_and_view_report(self):
        _,data,_=self.create()
        self.assertIn('INC-',data.decode('utf-8'))
        self.assertIn('Alta',data.decode('utf-8'))
    def test_registration_invalid_returns_422(self):
        code,data,_=self.request('/tickets',sample(description='corto'))
        self.assertEqual(code,422);self.assertIn('Revisa estos datos',data.decode('utf-8'))
    def test_report_is_listed_on_dashboard(self):
        self.create();_,body,_=self.request('/')
        self.assertIn('Sin acceso a la red',body.decode('utf-8'))
    def test_status_change_visible_in_timeline(self):
        self.create()
        code,body,_=self.request('/ticket/1/status',{'status':'En proceso','note':'Se inició la revisión del laboratorio'})
        self.assertEqual(code,200)
        self.assertIn('Se inició la revisión',body.decode('utf-8'))
    def test_invalid_transition_returns_409(self):
        self.create()
        code,_,_=self.request('/ticket/1/status',{'status':'Resuelto','note':'Se realizó todo el mantenimiento'})
        self.assertEqual(code,409)
    def test_not_found_returns_404(self):
        self.assertEqual(self.request('/ticket/99999')[0],404)
    def test_filter_status(self):
        self.create()
        _,body,_=self.request('/?status=Resuelto')
        self.assertIn('Aún no hay incidencias',body.decode('utf-8'))
    def test_xss_content_is_escaped(self):
        self.create(title='<script>alert(1)</script> falla de equipo')
        _,body,_=self.request('/')
        self.assertIn('&lt;script&gt;',body.decode('utf-8'))
        self.assertNotIn('<script>',body.decode('utf-8'))
    def test_csv_export_fields_and_formula_sanitization(self):
        self.create(reporter='=HYPERLINK("x")')
        code,body,headers=self.request('/export.csv')
        self.assertEqual(code,200)
        self.assertIn('text/csv',headers.get('Content-Type'))
        rows=list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[1][4].startswith("'="))
    def test_css_served_and_content_type(self):
        code,_,headers=self.request('/static/style.css')
        self.assertEqual(code,200)
        self.assertIn('text/css',headers.get('Content-Type'))
    def test_security_headers_present(self):
        _,_,headers=self.request('/')
        self.assertEqual(headers.get('X-Content-Type-Options'),'nosniff')
        self.assertEqual(headers.get('X-Frame-Options'),'DENY')
    def test_wrong_post_route_404(self):
        self.assertEqual(self.request('/bad',sample())[0],404)


if __name__ == '__main__':
    unittest.main()
