"""Tests automatisés du moteur Safety (T5 à T7, T9, T11, T15, T16).

Lancement depuis la racine du dépôt :
    python -m unittest discover tests

Les tests écrivent leur daily_score.json dans un dossier temporaire,
jamais dans output/.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Rend safety_engine importable quand les tests sont lancés depuis la racine.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import safety_engine  # noqa: E402

DATA = ROOT / "data"
EXTENDED = DATA / "demo_extended"
ERRORS = DATA / "test_errors"
HEADER = "timestamp,acc_x,acc_y,acc_z\n"


class SafetyTestCase(unittest.TestCase):
    """Fournit un dossier temporaire et des raccourcis d'exécution."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.output = self.folder / "out" / "daily_score.json"

    def run_engine(self, csv_path):
        """Exécute le moteur et renvoie (rapport, logs capturés)."""
        with self.assertLogs("gml.safety", level="INFO") as captured:
            report = safety_engine.run_safety(csv_path, self.output)
        return report, captured.records

    def write_csv(self, lines):
        path = self.folder / "accelerometer_data.csv"
        path.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8")
        return path


def events_of(report, kind):
    return [e for e in report["events"] if e["type"] == kind]


def messages(records, level_name):
    return [r.getMessage() for r in records if r.levelname == level_name]


class TestSafetyAnnexe3(SafetyTestCase):
    """Données de l'énoncé (Annexe 3)."""

    def test_t5_un_freinage_violent_a_1678880005(self):
        report, records = self.run_engine(DATA / "accelerometer_data.csv")
        self.assertEqual(report["events"], [{
            "type": "HARSH_BRAKING",
            "timestamp": 1678880005,
            "peak_acc_y_ms2": -3.45,
            "duration_samples": 1,
        }])
        self.assertEqual(
            messages(records, "ALERT SAFETY"),
            ["1678880005 freinage violent : acc_y = -3.45 m/s² (seuil -2.5)"],
        )

    def test_t6_le_bruit_ne_produit_aucun_evenement(self):
        path = self.write_csv([
            "1678880003,0.10,-0.50,9.79",
            "1678880004,0.09,-0.80,9.81",
            "1678880006,0.15,-1.20,9.75",
        ])
        report, _ = self.run_engine(path)
        self.assertEqual(report["events"], [])
        self.assertEqual(report["score"]["value"], 100)

    def test_t9_daily_score_json_conforme(self):
        self.run_engine(DATA / "accelerometer_data.csv")
        written = json.loads(self.output.read_text(encoding="utf-8"))
        for field in ("vehicle_id", "date", "source_file", "threshold_ms2",
                      "samples", "events", "summary", "score", "data_volume"):
            self.assertIn(field, written)
        self.assertEqual(written["vehicle_id"], "TRUCK-DEMO")
        self.assertEqual(written["date"], "2023-03-15")
        self.assertEqual(written["source_file"], "accelerometer_data.csv")
        self.assertEqual(written["threshold_ms2"], -2.5)
        self.assertEqual(written["samples"], {"total": 10, "valid": 10, "invalid": 0})
        self.assertEqual(written["summary"], {"harsh_braking_count": 1})
        self.assertEqual(written["score"]["value"], 90)
        self.assertEqual(written["score"]["max"], 100)

    def test_t15_bloc_data_volume_exact(self):
        report, _ = self.run_engine(DATA / "accelerometer_data.csv")
        volume = report["data_volume"]
        self.assertEqual(volume["raw_lines_per_day"], 3_600_000)
        self.assertEqual(volume["raw_bytes_per_day"], 104_400_000)
        # La taille de l'agrégat est celle du rapport sans le bloc data_volume.
        without_block = {k: v for k, v in report.items() if k != "data_volume"}
        expected_size = len(
            json.dumps(without_block, ensure_ascii=False, indent=2).encode("utf-8")
        )
        self.assertEqual(volume["aggregate_bytes"], expected_size)
        self.assertLess(volume["aggregate_bytes"], 1024)  # moins de 1 Ko
        self.assertEqual(
            volume["reduction_ratio"], round(104_400_000 / expected_size)
        )


class TestSafetyDonneesEtendues(SafetyTestCase):
    """Données supplémentaires (data/demo_extended)."""

    def test_t11_freinage_sur_trois_echantillons(self):
        report, records = self.run_engine(EXTENDED / "accelerometer_data.csv")
        self.assertEqual(report["events"], [
            {"type": "HARSH_BRAKING", "timestamp": 1678880005,
             "peak_acc_y_ms2": -3.45, "duration_samples": 1},
            {"type": "HARSH_BRAKING", "timestamp": 1678880015,
             "peak_acc_y_ms2": -4.10, "duration_samples": 3},
        ])
        self.assertEqual(report["score"]["value"], 80)
        self.assertEqual(len(messages(records, "ALERT SAFETY")), 2)


class TestSafetyRobustesse(SafetyTestCase):
    """Cas d'erreur du paragraphe 7 des spécifications."""

    def test_t7_csv_corrompu_sans_plantage(self):
        report, records = self.run_engine(ERRORS / "accelerometer_data.csv")
        self.assertEqual(report["samples"], {"total": 10, "valid": 8, "invalid": 2})
        self.assertEqual(messages(records, "WARNING"), [
            "Ligne 4 ignorée : acc_y non numérique",
            "Ligne 10 ignorée : champ manquant",
        ])
        self.assertEqual(report["summary"]["harsh_braking_count"], 1)

    def test_t16_ligne_invalide_ne_coupe_pas_l_evenement(self):
        path = self.write_csv([
            "1678880015,0.12,-2.90,9.70",
            "1678880016,0.10,abc,9.68",
            "1678880017,0.11,-4.10,9.66",
            "1678880018,0.10,-0.60,9.80",
        ])
        report, _ = self.run_engine(path)
        self.assertEqual(report["events"], [{
            "type": "HARSH_BRAKING",
            "timestamp": 1678880015,
            "peak_acc_y_ms2": -4.10,
            "duration_samples": 2,
        }])
        self.assertEqual(report["samples"]["invalid"], 1)

    def test_evenement_en_cours_en_fin_de_fichier(self):
        path = self.write_csv(["1678880015,0.12,-2.90,9.70"])
        report, _ = self.run_engine(path)
        self.assertEqual(report["summary"]["harsh_braking_count"], 1)

    def test_fichier_absent(self):
        with self.assertRaises(safety_engine.SafetyError) as context:
            safety_engine.run_safety(self.folder / "absent.csv", self.output)
        self.assertIn("Fichier introuvable", str(context.exception))

    def test_en_tete_inattendu(self):
        path = self.folder / "bad.csv"
        path.write_text("time,x,y,z\n1,0,0,9.81\n", encoding="utf-8")
        with self.assertRaises(safety_engine.SafetyError) as context:
            safety_engine.run_safety(path, self.output)
        self.assertEqual(
            str(context.exception),
            "En-tête CSV attendu : timestamp,acc_x,acc_y,acc_z",
        )

    def test_csv_vide(self):
        path = self.folder / "empty.csv"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(safety_engine.SafetyError):
            safety_engine.run_safety(path, self.output)

    def test_score_minimum_zero(self):
        # 11 freinages séparés par un retour à la normale : 100 - 110 -> 0.
        lines = []
        for index in range(11):
            lines.append(f"{1678880000 + 2 * index},0.10,-3.00,9.80")
            lines.append(f"{1678880001 + 2 * index},0.10,0.00,9.80")
        report, _ = self.run_engine(self.write_csv(lines))
        self.assertEqual(report["summary"]["harsh_braking_count"], 11)
        self.assertEqual(report["score"]["value"], 0)


if __name__ == "__main__":
    unittest.main()
