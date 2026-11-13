"""Tests automatisés du moteur ZFE (T1 à T4, T13, T14 et cas d'erreur).

Lancement depuis la racine du dépôt :
    python -m unittest discover tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Rend zfe_engine importable quand les tests sont lancés depuis la racine.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import zfe_engine  # noqa: E402

DATA = ROOT / "data"
EXTENDED = DATA / "demo_extended"
ERRORS = DATA / "test_errors"


def run_and_capture(test_case, data_dir):
    """Exécute le moteur sur un dossier et renvoie (récapitulatif, logs)."""
    with test_case.assertLogs("gml.zfe", level="INFO") as captured:
        summary = zfe_engine.run_zfe(
            data_dir / "lyon_polygon.json", data_dir / "truck_gps.json"
        )
    return summary, captured.records


def messages(records, level_name):
    """Filtre les messages de log d'un niveau donné."""
    return [r.getMessage() for r in records if r.levelname == level_name]


class TestZfeAnnexe3(unittest.TestCase):
    """Données de l'énoncé (Annexe 3)."""

    def test_t1_une_seule_alerte_au_point_4(self):
        summary, records = run_and_capture(self, DATA)
        alerts = messages(records, "ALERT ZFE")
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].startswith("#4 08:00:30"))
        self.assertEqual(summary["alerts"], 1)

    def test_t2_points_hors_bounding_box_sans_ray_casting(self):
        polygon = zfe_engine.load_polygon(DATA / "lyon_polygon.json")
        bounding_box = zfe_engine.compute_bounding_box(polygon)
        self.assertEqual(bounding_box, (45.73, 45.79, 4.80, 4.88))
        # On remplace le ray casting par un espion : il ne doit pas être appelé.
        with mock.patch.object(zfe_engine, "point_in_polygon") as ray_casting:
            for lat, lon in [(45.81, 4.75), (45.80, 4.78)]:  # points 1 et 2
                status, detail = zfe_engine.classify_point(
                    lat, lon, polygon, bounding_box
                )
                self.assertEqual((status, detail), ("OUT", "hors bounding box"))
            ray_casting.assert_not_called()

    def test_t3_point_3_dans_la_boite_hors_polygone_ecart_signale(self):
        summary, records = run_and_capture(self, DATA)
        point_3 = [m for m in messages(records, "INFO") if m.startswith("#3 ")]
        self.assertEqual(len(point_3), 1)
        self.assertIn("OUT (dans la bounding box, hors polygone)", point_3[0])
        self.assertIn("écart : énoncé attend « IN (Bordure) »", point_3[0])
        self.assertEqual(summary["mismatches"], 1)

    def test_t4_pas_d_alerte_en_double_au_point_5(self):
        _, records = run_and_capture(self, DATA)
        point_5 = [m for m in messages(records, "INFO") if m.startswith("#5 ")]
        self.assertEqual(
            point_5, ["#5 08:00:40 IN (déjà en zone, pas de nouvelle alerte)"]
        )


class TestZfeDonneesEtendues(unittest.TestCase):
    """Données supplémentaires (data/demo_extended) : sortie et ré-entrée."""

    def test_t13_sortie_de_zone_au_point_6(self):
        summary, records = run_and_capture(self, EXTENDED)
        exits = messages(records, "INFO ZFE")
        self.assertEqual(len(exits), 1)
        self.assertTrue(exits[0].startswith("#6 08:00:50"))
        self.assertIn("sortie de zone à faibles émissions", exits[0])
        self.assertEqual(summary["exits"], 1)

    def test_t14_re_entree_declenche_une_nouvelle_alerte(self):
        summary, records = run_and_capture(self, EXTENDED)
        alerts = messages(records, "ALERT ZFE")
        self.assertEqual(len(alerts), 2)
        self.assertTrue(alerts[0].startswith("#4 "))
        self.assertTrue(alerts[1].startswith("#8 08:01:10"))
        self.assertEqual(summary["alerts"], 2)


class TestZfeRobustesse(unittest.TestCase):
    """Cas d'erreur du paragraphe 7 des spécifications."""

    def write_json(self, folder, name, content):
        path = Path(folder) / name
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def test_point_gps_sans_lat_ignore(self):
        summary, records = run_and_capture(self, ERRORS)
        self.assertEqual(
            messages(records, "WARNING"), ["Point #2 ignoré : lat manquante"]
        )
        self.assertEqual(summary["points_ignored"], 1)
        self.assertEqual(summary["alerts"], 1)  # les autres points sont traités

    def test_fichier_absent(self):
        with self.assertRaises(zfe_engine.ZfeError) as context:
            zfe_engine.run_zfe(DATA / "absent.json", DATA / "truck_gps.json")
        self.assertIn("Fichier introuvable", str(context.exception))

    def test_json_malforme(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lyon_polygon.json"
            path.write_text('[\n  { "lat": 45.78,\n', encoding="utf-8")
            with self.assertRaises(zfe_engine.ZfeError) as context:
                zfe_engine.load_polygon(path)
        self.assertIn("JSON invalide dans lyon_polygon.json", str(context.exception))

    def test_polygone_de_moins_de_3_sommets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_json(
                folder,
                "p.json",
                [{"lat": 45.78, "lon": 4.80}, {"lat": 45.79, "lon": 4.86}],
            )
            with self.assertRaises(zfe_engine.ZfeError) as context:
                zfe_engine.load_polygon(path)
        self.assertEqual(
            str(context.exception), "Polygone invalide : 3 sommets minimum"
        )

    def test_polygone_non_ferme_est_ferme_automatiquement(self):
        closed = json.loads((DATA / "lyon_polygon.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_json(folder, "p.json", closed[:-1])
            with self.assertLogs("gml.zfe", level="WARNING") as captured:
                polygon = zfe_engine.load_polygon(path)
        self.assertEqual(polygon[0], polygon[-1])
        self.assertEqual(len(polygon), 6)
        self.assertEqual(
            captured.records[0].getMessage(), "Polygone fermé automatiquement"
        )


if __name__ == "__main__":
    unittest.main()
