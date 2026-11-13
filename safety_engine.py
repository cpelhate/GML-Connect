"""Moteur Safety (Physics) - Mission 2 : boîte noire télématique.

Ce module lit les mesures brutes de l'accéléromètre et en extrait le
micro-comportement démontré par le POC : le freinage violent, une forte
décélération sur l'axe longitudinal (acc_y).

Les mesures brutes ne quittent jamais la tablette. Seul un petit fichier
agrégé, daily_score.json, est destiné à être transmis : c'est ce qui évite
de saturer le forfait 4G (contrainte C5).
"""

import csv
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

# --- Niveau de log propre au moteur Safety ----------------------------------
ALERT_SAFETY = 36  # entre WARNING (30) et ERROR (40)
logging.addLevelName(ALERT_SAFETY, "ALERT SAFETY")

# --- Paramètres (ajustables sans toucher à l'algorithme) --------------------
THRESHOLD_MS2 = -2.5            # freinage violent si acc_y < -2.5 m/s² (RG-S3)

SCORE_MAX = 100
BRAKING_PENALTY = 10            # points retirés par freinage violent

# Hypothèses du calcul de volume (question 3 de la vidéo).
SAMPLING_HZ = 100               # Annexe 1 : 100 points par seconde
DRIVING_HOURS_PER_DAY = 10      # hypothèse de la copie
BYTES_PER_RAW_LINE = 29         # taille d'une ligne CSV horodatée

EXPECTED_HEADER = ["timestamp", "acc_x", "acc_y", "acc_z"]

logger = logging.getLogger("gml.safety")


class SafetyError(Exception):
    """Erreur bloquante du moteur Safety (fichier absent, en-tête invalide).

    main.py intercepte cette exception pour afficher un message [ERROR] clair
    au lieu d'une trace d'erreur Python.
    """


def _format_number(value):
    """Affiche un entier avec une espace entre les milliers : 104 400 000."""
    return f"{value:,}".replace(",", " ")


def _parse_row(row):
    """Convertit une ligne CSV en (timestamp, acc_x, acc_y, acc_z).

    Lève ValueError avec la cause si la ligne est inexploitable (RG-S2).
    """
    if len(row) < len(EXPECTED_HEADER):
        raise ValueError("champ manquant")
    values = []
    for name, raw in zip(EXPECTED_HEADER, row):
        try:
            number = float(raw)
        except ValueError:
            raise ValueError(f"{name} non numérique") from None
        # « nan » et « inf » sont acceptés par float() : on les refuse.
        if not math.isfinite(number):
            raise ValueError(f"{name} non fini")
        # À 100 Hz, l'horodatage a des décimales (1678880000.01).
        # Un horodatage entier reste un entier dans le JSON (1678880005).
        if name == "timestamp" and number.is_integer():
            number = int(number)
        values.append(number)
    return tuple(values)


def _track(current, timestamp, value, exceeded):
    """RG-S5 : regroupe les échantillons consécutifs hors seuil en un événement.

    - current  : l'événement en cours pour ce type, ou None.
    - exceeded : True si l'échantillon dépasse le seuil.
    Renvoie (événement en cours, événement terminé). L'événement est terminé
    dès qu'un échantillon repasse sous le seuil.

    À 100 Hz, un freinage dure plusieurs dizaines d'échantillons : sans ce
    regroupement, il serait compté des dizaines de fois.
    """
    if exceeded:
        if current is None:
            # Début d'un nouvel événement.
            return {
                "timestamp": timestamp,
                "peak": value,
                "peak_timestamp": timestamp,
                "duration_samples": 1,
            }, None
        current["duration_samples"] += 1
        if abs(value) > abs(current["peak"]):
            current["peak"] = value  # le pic est la valeur la plus forte
            current["peak_timestamp"] = timestamp
        return current, None
    return None, current


def _data_volume(aggregate_bytes):
    """Compare le volume brut d'une journée au volume du fichier agrégé."""
    raw_lines = SAMPLING_HZ * 3600 * DRIVING_HOURS_PER_DAY
    raw_bytes = raw_lines * BYTES_PER_RAW_LINE
    return {
        "hypotheses": {
            "sampling_hz": SAMPLING_HZ,
            "driving_hours_per_day": DRIVING_HOURS_PER_DAY,
            "bytes_per_raw_line": BYTES_PER_RAW_LINE,
        },
        "raw_lines_per_day": raw_lines,
        "raw_bytes_per_day": raw_bytes,
        "aggregate_bytes": aggregate_bytes,
        "aggregate_bytes_note": "taille du rapport hors bloc data_volume",
        "reduction_ratio": round(raw_bytes / aggregate_bytes),
    }


def run_safety(csv_path, output_path, vehicle_id="TRUCK-DEMO"):
    """Analyse le CSV de l'accéléromètre, écrit daily_score.json, renvoie le rapport.

    Lève SafetyError si le fichier est absent, vide, mal structuré ou sans
    aucune mesure valide. Une ligne invalide isolée, elle, est ignorée et
    comptée : le traitement continue.
    """
    csv_path = Path(csv_path)
    output_path = Path(output_path)

    total = valid = invalid = 0
    first_timestamp = None
    events = []
    braking = None  # freinage en cours, ou None

    def close(event):
        """Enregistre un freinage terminé et émet son log (RG-S6)."""
        if event is None:
            return
        events.append({
            "type": "HARSH_BRAKING",
            "timestamp": event["timestamp"],  # horodatage du début de l'événement
            "peak_acc_y_ms2": event["peak"],
            "peak_timestamp": event["peak_timestamp"],  # instant du pic
            "duration_samples": event["duration_samples"],
        })
        logger.log(
            ALERT_SAFETY, "%s freinage violent : acc_y = %.2f m/s² (seuil %s)",
            event["timestamp"], event["peak"], THRESHOLD_MS2,
        )

    try:
        # utf-8-sig tolère la marque d'ordre d'octets ajoutée par certains tableurs.
        with open(csv_path, newline="", encoding="utf-8-sig") as file:
            # RG-S1 : csv.reader lit une ligne à la fois. Le fichier n'est
            # jamais chargé en entier, la mémoire utilisée reste constante.
            reader = csv.reader(file)
            header = next(reader, None)
            if header is None or [h.strip() for h in header] != EXPECTED_HEADER:
                raise SafetyError(
                    "En-tête CSV attendu : " + ",".join(EXPECTED_HEADER)
                )

            # Une seule boucle sur les lignes : coût O(N), linéaire.
            for row in reader:
                if not row:
                    continue  # ligne vide, par exemple en fin de fichier
                total += 1
                try:
                    timestamp, _, acc_y, _ = _parse_row(row)
                except ValueError as error:
                    invalid += 1
                    logger.warning(
                        "Ligne %d ignorée : %s", reader.line_num, error
                    )
                    # La ligne est sautée sans toucher aux événements en
                    # cours : une ligne corrompue ne coupe pas un freinage.
                    continue

                valid += 1
                if first_timestamp is None:
                    first_timestamp = timestamp

                # RG-S3 et RG-S4 : comparaison directe au seuil, sans moyenne
                # glissante (elle ferait disparaître un pic isolé).
                braking, finished = _track(
                    braking, timestamp, acc_y, acc_y < THRESHOLD_MS2
                )
                close(finished)
    except FileNotFoundError:
        raise SafetyError(f"Fichier introuvable : {csv_path.as_posix()}") from None

    # Aucune mesure exploitable : pas de score. Un capteur en panne ne doit
    # jamais produire un score parfait de 100/100.
    if valid == 0:
        raise SafetyError("Aucune mesure exploitable : score non calculé")

    # Fin de fichier : un événement encore en cours est clôturé.
    close(braking)

    braking_count = len(events)
    score = max(0, SCORE_MAX - BRAKING_PENALTY * braking_count)

    report = {
        "vehicle_id": vehicle_id,
        # Date déduite du premier horodatage valide (Unix, UTC).
        "date": (
            datetime.fromtimestamp(first_timestamp, tz=timezone.utc).date().isoformat()
            if first_timestamp is not None else None
        ),
        "source_file": csv_path.name,
        "threshold_ms2": THRESHOLD_MS2,
        "samples": {"total": total, "valid": valid, "invalid": invalid},
        "events": events,
        "summary": {"harsh_braking_count": braking_count},
        "score": {
            "value": score,
            "max": SCORE_MAX,
            "rule": (
                f"{SCORE_MAX} - {BRAKING_PENALTY} x nombre de freinages "
                "violents, minimum 0"
            ),
        },
    }

    # Volume : on mesure le rapport avant d'y ajouter le bloc data_volume,
    # car un fichier ne peut pas contenir sa propre taille exacte.
    aggregate_bytes = len(_serialize(report))
    report["data_volume"] = _data_volume(aggregate_bytes)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_serialize(report))

    logger.info(
        "Échantillons : %d lus, %d valides, %d invalides", total, valid, invalid
    )
    logger.info(
        "Score du jour : %d/%d (%d freinage(s) violent(s))",
        score, SCORE_MAX, braking_count,
    )
    volume = report["data_volume"]
    logger.info(
        "Volume brut estimé : %s octets/jour (%d Hz, %d h) ; agrégat : %s octets ; "
        "réduction d'un facteur %s",
        _format_number(volume["raw_bytes_per_day"]), SAMPLING_HZ,
        DRIVING_HOURS_PER_DAY, _format_number(volume["aggregate_bytes"]),
        _format_number(volume["reduction_ratio"]),
    )
    logger.info("Rapport écrit : %s", output_path.as_posix())
    return report


def _serialize(report):
    """Sérialise le rapport en JSON lisible, encodé en UTF-8."""
    return json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
