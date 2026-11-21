"""Verifica demo_seed.py: inserta datos ficticios una sola vez (idempotente)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from db import Repository

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_SEED = REPO_ROOT / "demo_seed.py"


class DemoSeedScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "demo.db"

    def tearDown(self):
        self.tmp.cleanup()

    def run_seed(self):
        return subprocess.run(
            [sys.executable, str(DEMO_SEED), "--db", str(self.db_path)],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=30,
        )

    def test_first_run_inserts_five_tickets_with_expected_states(self):
        result = self.run_seed()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Insertados cinco reportes", result.stdout)

        repo = Repository(self.db_path)
        metrics = repo.metrics()
        self.assertEqual(metrics["total"], 5)
        self.assertEqual(metrics["open"], 3)
        self.assertEqual(metrics["progress"], 1)
        self.assertEqual(metrics["resolved"], 1)

        projector = repo.get_ticket(2)  # indice 1: "Proyector sin señal de video"
        self.assertEqual(projector["status"], "En proceso")
        self.assertEqual(len(repo.get_events(2)), 2)

        keyboard = repo.get_ticket(4)  # indice 3: "Teclado con teclas sin respuesta"
        self.assertEqual(keyboard["status"], "Resuelto")
        self.assertEqual(len(repo.get_events(4)), 3)

        for ticket_id in (1, 3, 5):
            with self.subTest(ticket_id=ticket_id):
                ticket = repo.get_ticket(ticket_id)
                self.assertEqual(ticket["status"], "Abierto")
                self.assertEqual(len(repo.get_events(ticket_id)), 1)

    def test_second_run_does_not_duplicate(self):
        first = self.run_seed()
        self.assertEqual(first.returncode, 0, first.stderr)

        second = self.run_seed()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("no está vacía", second.stdout)

        repo = Repository(self.db_path)
        self.assertEqual(repo.metrics()["total"], 5)


if __name__ == "__main__":
    unittest.main()
