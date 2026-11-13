"""Tests de bout en bout du POC (T8, robustesse, journal, options).

main.py est lancé comme le ferait un utilisateur, dans un sous-processus.
Le dossier courant est un dossier temporaire : output/ y est créé, le
dossier output/ du dépôt n'est jamais modifié par les tests.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


class TestMain(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workdir = Path(folder.name)

    def run_poc(self, data_dir, *options):
        """Lance main.py et renvoie (code de sortie, sortie console)."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--data-dir", str(data_dir),
             *options],
            cwd=self.workdir,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        return result.returncode, result.stdout + result.stderr

    def copy_data_without(self, missing_file):
        """Copie les données de l'énoncé en retirant un fichier."""
        target = self.workdir / "data"
        target.mkdir()
        for name in ("lyon_polygon.json", "truck_gps.json", "accelerometer_data.csv"):
            if name != missing_file:
                shutil.copy(DATA / name, target / name)
        return target

    def read_score(self):
        path = self.workdir / "output" / "daily_score.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_execution_nominale(self):
        code, output = self.run_poc(DATA)
        self.assertEqual(code, 0)
        self.assertEqual(output.count("[ALERT ZFE]"), 1)
        self.assertIn("[ALERT ZFE] #4 08:00:30", output)
        self.assertIn("[ALERT SAFETY] 1678880005 freinage violent", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual(self.read_score()["score"]["value"], 90)

    def test_donnees_corrompues_sans_plantage(self):
        code, output = self.run_poc(DATA / "test_errors")
        self.assertEqual(code, 0)  # les deux moteurs aboutissent malgré tout
        self.assertIn("[WARNING] Point #2 ignoré : lat manquante", output)
        self.assertIn("[WARNING] Ligne 4 ignorée : acc_y non numérique", output)
        self.assertIn("[WARNING] Ligne 10 ignorée : champ manquant", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual(self.read_score()["samples"]["invalid"], 2)

    def test_t8_trace_gps_absente_le_moteur_safety_s_execute(self):
        code, output = self.run_poc(self.copy_data_without("truck_gps.json"))
        self.assertEqual(code, 1)
        self.assertIn("[ERROR] Fichier introuvable", output)
        self.assertIn("Moteur ZFE    : ÉCHEC", output)
        self.assertIn("[ALERT SAFETY] 1678880005", output)
        self.assertNotIn("Traceback", output)

    def test_t8_csv_absent_le_moteur_zfe_s_execute(self):
        code, output = self.run_poc(self.copy_data_without("accelerometer_data.csv"))
        self.assertEqual(code, 1)
        self.assertIn("[ERROR] Fichier introuvable", output)
        self.assertIn("Moteur Safety : ÉCHEC", output)
        self.assertIn("[ALERT ZFE] #4 08:00:30", output)
        self.assertNotIn("Traceback", output)

    def test_json_malforme_sans_plantage(self):
        data_dir = self.copy_data_without(None)
        (data_dir / "lyon_polygon.json").write_text('[\n  { "lat": 45.78,\n',
                                                    encoding="utf-8")
        code, output = self.run_poc(data_dir)
        self.assertEqual(code, 1)
        self.assertIn("[ERROR] JSON invalide dans lyon_polygon.json", output)
        self.assertNotIn("Traceback", output)

    def test_journal_en_ajout_avec_separateur(self):
        self.run_poc(DATA)
        self.run_poc(DATA)
        log = (self.workdir / "output" / "poc.log").read_text(encoding="utf-8")
        self.assertEqual(log.count("===== POC GML Edge - exécution du"), 2)
        self.assertEqual(log.count("[ALERT ZFE]"), 2)
        # Dans le fichier, chaque ligne commence par la date et l'heure.
        for line in log.splitlines():
            self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[")

    def test_option_vehicle_id(self):
        self.run_poc(DATA, "--vehicle-id", "TRUCK-42")
        self.assertEqual(self.read_score()["vehicle_id"], "TRUCK-42")

    def test_donnees_etendues(self):
        code, output = self.run_poc(DATA / "demo_extended")
        self.assertEqual(code, 0)
        self.assertEqual(output.count("[ALERT ZFE]"), 2)
        self.assertEqual(output.count("[INFO ZFE]"), 1)
        self.assertEqual(self.read_score()["score"]["value"], 80)


if __name__ == "__main__":
    unittest.main()
