"""Point d'entrée du POC GML Edge.

Lance les deux moteurs l'un après l'autre, sans aucun appel réseau :
  1. Moteur ZFE (Geo)        -> alerte [ALERT ZFE] à l'entrée en zone ;
  2. Moteur Safety (Physics) -> événements [ALERT SAFETY] et daily_score.json.

Chaque moteur est isolé : si l'un échoue, l'autre s'exécute quand même, et
le programme ne s'arrête jamais sur une trace d'erreur Python.

Exemples (depuis la racine du dépôt) :
    python main.py
    python main.py --realtime
    python main.py --data-dir data/test_errors
    python main.py --data-dir data/demo_extended --vehicle-id TRUCK-42
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import safety_engine
import zfe_engine

OUTPUT_DIR = Path("output")
LOG_FILE = OUTPUT_DIR / "poc.log"
SCORE_FILE = OUTPUT_DIR / "daily_score.json"

logger = logging.getLogger("gml")


def parse_arguments(argv=None):
    """Déclare et lit les options de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="POC GML Edge : moteur ZFE et moteur Safety, hors ligne."
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data"),
        help="dossier des fichiers d'entrée (défaut : data)",
    )
    parser.add_argument(
        "--realtime", action="store_true",
        help="rejoue la trace GPS avec une pause d'une seconde entre chaque point",
    )
    parser.add_argument(
        "--vehicle-id", default="TRUCK-DEMO",
        help="identifiant du camion écrit dans daily_score.json (défaut : TRUCK-DEMO)",
    )
    return parser.parse_args(argv)


def configure_logging():
    """Envoie les logs vers la console et vers output/poc.log.

    - Console : « [NIVEAU] message », le format exigé par l'énoncé.
    - Fichier : même ligne, précédée de la date et de l'heure. Le fichier est
      ouvert en ajout (mode « a ») : chaque exécution s'écrit à la suite des
      précédentes, ce qui garde l'historique des démonstrations.
    """
    # Un caractère non affichable par la console est remplacé au lieu de
    # provoquer une erreur d'encodage pendant la démonstration.
    sys.stdout.reconfigure(errors="replace")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    log_file = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    log_file.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Les moteurs écrivent dans « gml.zfe » et « gml.safety » : configurer
    # leur parent « gml » suffit pour les deux.
    logger.setLevel(logging.INFO)
    logger.addHandler(console)
    logger.addHandler(log_file)


def run_engine(name, engine):
    """Exécute un moteur et renvoie son résultat, ou None s'il a échoué.

    Aucune exception ne remonte : l'erreur est écrite en [ERROR] et le
    programme continue avec l'autre moteur.
    """
    logger.info("--- Moteur %s ---", name)
    try:
        return engine()
    except (zfe_engine.ZfeError, safety_engine.SafetyError) as error:
        # Erreur prévue : fichier absent, JSON invalide, en-tête inattendu...
        logger.error("%s", error)
    except Exception as error:
        # Filet de sécurité : toute autre erreur est aussi interceptée pour
        # que la démonstration ne se termine jamais sur une trace Python.
        logger.error("Erreur inattendue (%s : %s)", type(error).__name__, error)
    logger.error("Moteur %s ignoré", name)
    return None


def main(argv=None):
    """Lance les deux moteurs et renvoie le code de sortie (0 ou 1)."""
    arguments = parse_arguments(argv)
    configure_logging()
    data_dir = arguments.data_dir

    # Ligne séparatrice datée : délimite chaque exécution dans poc.log.
    logger.info(
        "===== POC GML Edge - exécution du %s - données : %s =====",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), data_dir.as_posix(),
    )

    zfe = run_engine(
        "ZFE (Geo)",
        lambda: zfe_engine.run_zfe(
            data_dir / "lyon_polygon.json",
            data_dir / "truck_gps.json",
            realtime=arguments.realtime,
        ),
    )
    safety = run_engine(
        "Safety (Physics)",
        lambda: safety_engine.run_safety(
            data_dir / "accelerometer_data.csv",
            SCORE_FILE,
            vehicle_id=arguments.vehicle_id,
        ),
    )

    logger.info("--- Récapitulatif ---")
    if zfe is None:
        logger.info("Moteur ZFE    : ÉCHEC")
    else:
        logger.info(
            "Moteur ZFE    : OK - %d point(s), %d alerte(s), %d sortie(s), "
            "%d point(s) ignoré(s), %d écart(s) avec l'énoncé",
            zfe["points_total"], zfe["alerts"], zfe["exits"],
            zfe["points_ignored"], zfe["mismatches"],
        )
    if safety is None:
        logger.info("Moteur Safety : ÉCHEC")
    else:
        logger.info(
            "Moteur Safety : OK - score %d/%d, %d freinage(s) violent(s), "
            "%d virage(s) brusque(s), %d ligne(s) invalide(s)",
            safety["score"]["value"], safety["score"]["max"],
            safety["summary"]["harsh_braking_count"],
            safety["summary"]["harsh_cornering_count"],
            safety["samples"]["invalid"],
        )

    logging.shutdown()  # vide et ferme le fichier de log
    # Code de sortie : 0 si les deux moteurs ont abouti, 1 sinon.
    return 0 if zfe is not None and safety is not None else 1


if __name__ == "__main__":
    sys.exit(main())
