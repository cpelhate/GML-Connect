"""Moteur Safety (Physics) - Mission 2 : boîte noire télématique.

Ce module lit les mesures brutes de l'accéléromètre et en extrait les
micro-comportements exigés par l'assureur :
  - freinage violent : forte décélération sur l'axe longitudinal (acc_y) ;
  - virage brusque   : forte accélération sur l'axe latéral (acc_x).

Les mesures brutes ne quittent jamais la tablette. Seul un petit fichier
agrégé, daily_score.json, est destiné à être transmis : c'est ce qui évite
de saturer le forfait 4G (contrainte C5).
"""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# --- Niveau de log propre au moteur Safety ----------------------------------
ALERT_SAFETY = 36  # entre WARNING (30) et ERROR (40)
logging.addLevelName(ALERT_SAFETY, "ALERT SAFETY")

# --- Paramètres (ajustables sans toucher à l'algorithme) --------------------
THRESHOLD_MS2 = -2.5            # freinage violent si acc_y < -2.5 m/s² (RG-S3)
CORNERING_THRESHOLD_MS2 = 2.5   # virage brusque si |acc_x| > 2.5 m/s²

SCORE_MAX = 100
BRAKING_PENALTY = 10            # points retirés par freinage violent
CORNERING_PENALTY = 5           # points retirés par virage brusque

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
            values.append(int(raw) if name == "timestamp" else float(raw))
        except ValueError:
            raise ValueError(f"{name} non numérique") from None
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
            return {"timestamp": timestamp, "peak": value, "duration_samples": 1}, None
        current["duration_samples"] += 1
        if abs(value) > abs(current["peak"]):
            current["peak"] = value  # le pic est la valeur la plus forte
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

    Lève SafetyError si le fichier est absent, vide ou mal structuré. Une
    ligne invalide, elle, est ignorée et comptée : le traitement continue.
    """
    csv_path = Path(csv_path)
    output_path = Path(output_path)

    total = valid = invalid = 0
    first_timestamp = None
    events = []
    braking = None    # freinage en cours, ou None
    cornering = None  # virage en cours, ou None

    def close(event, kind):
        """Enregistre un événement terminé et émet son log (RG-S6)."""
        if event is None:
            return
        if kind == "HARSH_BRAKING":
            peak_key, label, axis = "peak_acc_y_ms2", "freinage violent", "acc_y"
            threshold = f"{THRESHOLD_MS2}"
        else:
            peak_key, label, axis = "peak_acc_x_ms2", "virage brusque", "acc_x"
            threshold = f"±{CORNERING_THRESHOLD_MS2}"
        events.append({
            "type": kind,
            "timestamp": event["timestamp"],  # horodatage du début de l'événement
            peak_key: event["peak"],
            "duration_samples": event["duration_samples"],
        })
        logger.log(
            ALERT_SAFETY, "%s %s : %s = %.2f m/s² (seuil %s)",
            event["timestamp"], label, axis, event["peak"], threshold,
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
                    timestamp, acc_x, acc_y, _ = _parse_row(row)
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
                close(finished, "HARSH_BRAKING")

                cornering, finished = _track(
                    cornering, timestamp, acc_x,
                    abs(acc_x) > CORNERING_THRESHOLD_MS2,
                )
                close(finished, "HARSH_CORNERING")
    except FileNotFoundError:
        raise SafetyError(f"Fichier introuvable : {csv_path.as_posix()}") from None

    # Fin de fichier : un événement encore en cours est clôturé.
    close(braking, "HARSH_BRAKING")
    close(cornering, "HARSH_CORNERING")
    events.sort(key=lambda event: event["timestamp"])

    braking_count = sum(1 for e in events if e["type"] == "HARSH_BRAKING")
    cornering_count = len(events) - braking_count
    score = max(
        0,
        SCORE_MAX
        - BRAKING_PENALTY * braking_count
        - CORNERING_PENALTY * cornering_count,
    )

    report = {
        "vehicle_id": vehicle_id,
        # Date déduite du premier horodatage valide (Unix, UTC).
        "date": (
            datetime.fromtimestamp(first_timestamp, tz=timezone.utc).date().isoformat()
            if first_timestamp is not None else None
        ),
        "source_file": csv_path.name,
        "threshold_ms2": THRESHOLD_MS2,
        "cornering_threshold_ms2": CORNERING_THRESHOLD_MS2,
        "samples": {"total": total, "valid": valid, "invalid": invalid},
        "events": events,
        "summary": {
            "harsh_braking_count": braking_count,
            "harsh_cornering_count": cornering_count,
        },
        "score": {
            "value": score,
            "max": SCORE_MAX,
            "rule": (
                f"{SCORE_MAX} - {BRAKING_PENALTY} x freinages violents "
                f"- {CORNERING_PENALTY} x virages brusques, minimum 0"
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
        "Score du jour : %d/%d (%d freinage(s) violent(s), %d virage(s) brusque(s))",
        score, SCORE_MAX, braking_count, cornering_count,
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
