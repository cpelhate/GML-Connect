"""Moteur ZFE (Geo) - Mission 1 : conformité ZFE sans réseau.

Ce module répond à la question : « le camion vient-il d'entrer dans la zone
à faibles émissions ? », sans aucun appel réseau (contrainte C1).

Principe en deux temps, pour chaque point GPS :
  1. Bounding box : un test très rapide (4 comparaisons) qui élimine les
     points manifestement hors zone.
  2. Ray casting  : le test exact « point dans le polygone », exécuté
     seulement si le point est dans la bounding box.

Une machine d'état OUT/IN évite de répéter l'alerte : elle n'est émise
qu'au passage de OUT à IN (règle RG-Z4).
"""

import json
import logging
import time
from pathlib import Path

# --- Niveaux de log propres au moteur ZFE -----------------------------------
# Le module logging affiche le nom du niveau entre crochets. On déclare donc
# deux niveaux nommés pour obtenir exactement les préfixes [ALERT ZFE]
# (exigé par l'énoncé) et [INFO ZFE] (sortie de zone).
ALERT_ZFE = 35  # entre WARNING (30) et ERROR (40)
INFO_ZFE = 25   # entre INFO (20) et WARNING (30)
logging.addLevelName(ALERT_ZFE, "ALERT ZFE")
logging.addLevelName(INFO_ZFE, "INFO ZFE")

# --- Paramètres -------------------------------------------------------------
REALTIME_PAUSE_S = 1.0      # pause entre deux points en mode --realtime
MIN_DISTINCT_VERTICES = 3   # un polygone a au moins 3 sommets distincts

logger = logging.getLogger("gml.zfe")


class ZfeError(Exception):
    """Erreur bloquante du moteur ZFE (fichier absent, JSON ou polygone invalide).

    main.py intercepte cette exception pour afficher un message [ERROR] clair
    au lieu d'une trace d'erreur Python.
    """


# --- Chargement des fichiers ------------------------------------------------

def _load_json(path):
    """Lit un fichier JSON local et traduit les erreurs en message lisible."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        raise ZfeError(f"Fichier introuvable : {path.as_posix()}") from None
    except json.JSONDecodeError as error:
        raise ZfeError(
            f"JSON invalide dans {path.name} (ligne {error.lineno})"
        ) from None


def _read_coordinate(point, key):
    """Renvoie point[key] en nombre décimal, ou lève ValueError avec la cause."""
    if not isinstance(point, dict) or key not in point:
        raise ValueError(f"{key} manquante")
    value = point[key]
    # bool est exclu : en Python, True et False sont aussi des entiers.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} non numérique")
    return float(value)


def load_polygon(path):
    """Charge le polygone ZFE et renvoie une liste fermée de sommets (lat, lon).

    - Chaque sommet doit avoir une lat et une lon numériques.
    - RG-Z5 : si le dernier sommet diffère du premier, le polygone est fermé
      automatiquement et un avertissement est écrit.
    - Il faut au moins 3 sommets distincts, sinon ce n'est pas une surface.
    """
    data = _load_json(path)
    if not isinstance(data, list):
        raise ZfeError("Polygone invalide : une liste de sommets est attendue")

    polygon = []
    for index, vertex in enumerate(data, start=1):
        try:
            polygon.append(
                (_read_coordinate(vertex, "lat"), _read_coordinate(vertex, "lon"))
            )
        except ValueError as error:
            raise ZfeError(f"Polygone invalide : sommet {index}, {error}") from None

    if len(set(polygon)) < MIN_DISTINCT_VERTICES:
        raise ZfeError(
            f"Polygone invalide : {MIN_DISTINCT_VERTICES} sommets minimum"
        )

    if polygon[0] != polygon[-1]:
        polygon.append(polygon[0])
        logger.warning("Polygone fermé automatiquement")

    return polygon


# --- Algorithmes ------------------------------------------------------------

def compute_bounding_box(polygon):
    """RG-Z1 : calcule le rectangle englobant du polygone, une seule fois.

    Renvoie (lat_min, lat_max, lon_min, lon_max).
    """
    latitudes = [lat for lat, _ in polygon]
    longitudes = [lon for _, lon in polygon]
    return min(latitudes), max(latitudes), min(longitudes), max(longitudes)


def in_bounding_box(lat, lon, bounding_box):
    """RG-Z2 : pré-filtre en O(1), quatre comparaisons seulement."""
    lat_min, lat_max, lon_min, lon_max = bounding_box
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def point_in_polygon(lat, lon, polygon):
    """RG-Z3 : ray casting (point dans un polygone).

    On trace une demi-droite horizontale partant du point vers l'est
    (longitudes croissantes) et on compte les arêtes qu'elle traverse :
    nombre impair = le point est dedans, nombre pair = dehors.

    Coût : O(k), k étant le nombre de sommets du polygone (6 pour Lyon).
    """
    inside = False
    for index in range(len(polygon) - 1):
        lat1, lon1 = polygon[index]
        lat2, lon2 = polygon[index + 1]

        # RG-Z6 : condition semi-ouverte. L'arête est retenue si ses deux
        # extrémités sont de part et d'autre de la latitude du point. Avec
        # « > » des deux côtés, un sommet aligné avec le point n'est compté
        # qu'une fois, et une arête horizontale (lat1 == lat2) est ignorée,
        # ce qui écarte aussi la division par zéro ci-dessous.
        if (lat1 > lat) != (lat2 > lat):
            # Longitude où l'arête coupe la latitude du point (interpolation).
            crossing_lon = lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
            # Le croisement ne compte que s'il est à l'est du point.
            if lon < crossing_lon:
                inside = not inside
    return inside


def classify_point(lat, lon, polygon, bounding_box):
    """Renvoie (statut, détail) pour un point : bounding box puis ray casting."""
    if not in_bounding_box(lat, lon, bounding_box):
        return "OUT", "hors bounding box"
    if point_in_polygon(lat, lon, polygon):
        return "IN", "dans le polygone"
    return "OUT", "dans la bounding box, hors polygone"


# --- Traitement de la trace GPS ---------------------------------------------

def _expected_status(point):
    """Lit le champ « expected » de l'énoncé : 'IN', 'OUT' ou None.

    Ce champ sert uniquement à comparer avec le résultat calculé,
    jamais à décider.
    """
    expected = point.get("expected")
    if isinstance(expected, str):
        if expected.startswith("IN"):
            return "IN"
        if expected.startswith("OUT"):
            return "OUT"
    return None


def run_zfe(polygon_path, gps_path, realtime=False):
    """Exécute le moteur ZFE sur une trace GPS et renvoie un récapitulatif.

    Lève ZfeError si un fichier est absent ou inexploitable. Un point GPS
    invalide, lui, est simplement ignoré : les autres sont traités.
    """
    polygon = load_polygon(polygon_path)
    bounding_box = compute_bounding_box(polygon)  # calculée une seule fois
    logger.info(
        "Polygone chargé : %d sommets, bounding box lat %.4f à %.4f, "
        "lon %.4f à %.4f",
        len(polygon) - 1, *bounding_box,
    )

    trace = _load_json(gps_path)
    if not isinstance(trace, list):
        raise ZfeError("Trace GPS invalide : une liste de points est attendue")

    summary = {
        "points_total": len(trace),
        "points_ignored": 0,
        "alerts": 0,
        "exits": 0,
        "mismatches": 0,
    }
    state = "OUT"  # état initial : le camion est supposé hors zone

    # Une seule boucle sur les points : coût total O(m x k), linéaire en m.
    for position, point in enumerate(trace, start=1):
        if realtime and position > 1:
            time.sleep(REALTIME_PAUSE_S)

        label = point.get("id", position) if isinstance(point, dict) else position
        try:
            lat = _read_coordinate(point, "lat")
            lon = _read_coordinate(point, "lon")
        except ValueError as error:
            summary["points_ignored"] += 1
            logger.warning("Point #%s ignoré : %s", label, error)
            continue

        timestamp = point.get("timestamp", "--:--:--")
        status, detail = classify_point(lat, lon, polygon, bounding_box)

        # Comparaison avec l'énoncé : l'écart est signalé, pas corrigé.
        suffix = ""
        expected = _expected_status(point)
        if expected is not None and expected != status:
            summary["mismatches"] += 1
            suffix = f" - écart : énoncé attend « {point['expected']} »"

        # Machine d'état : seules les transitions déclenchent un log dédié.
        if state == "OUT" and status == "IN":
            summary["alerts"] += 1
            logger.log(
                ALERT_ZFE,
                "#%s %s lat %.4f lon %.4f : entrée en zone à faibles émissions%s",
                label, timestamp, lat, lon, suffix,
            )
        elif state == "IN" and status == "OUT":
            summary["exits"] += 1
            logger.log(
                INFO_ZFE,
                "#%s %s lat %.4f lon %.4f : sortie de zone à faibles émissions%s",
                label, timestamp, lat, lon, suffix,
            )
        elif status == "IN":
            logger.info(
                "#%s %s IN (déjà en zone, pas de nouvelle alerte)%s",
                label, timestamp, suffix,
            )
        else:
            logger.info("#%s %s OUT (%s)%s", label, timestamp, detail, suffix)

        state = status

    return summary
