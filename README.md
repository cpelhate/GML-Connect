# GML-Edge - POC GML Edge

Preuve de concept du pivot « Edge Computing Télématique » de GreenMove Logistics.
Un moteur autonome, qui tourne **sans réseau**, et qui prouve deux choses :

- **Mission 1 - Conformité ZFE** : l'entrée du camion dans la zone à faibles émissions de Lyon est détectée hors ligne et déclenche un log `[ALERT ZFE]`.
- **Mission 2 - Boîte noire télématique** : un freinage violent est isolé dans le signal bruité de l'accéléromètre, puis résumé dans `output/daily_score.json`.

## Prérequis

- Python 3.10 ou plus récent.
- Aucune dépendance externe : seule la bibliothèque standard est utilisée.
- Sous Windows, la commande peut être `py` au lieu de `python`.

## Installation

```bash
git clone https://github.com/cpelhate/GML-Edge.git
cd GML-Edge
```

## Lancement

Toutes les commandes se lancent depuis la racine du dépôt.

| Commande | Effet |
|---|---|
| `python main.py` | Exécution nominale sur les données de l'énoncé (Annexe 3) |
| `python main.py --realtime` | Rejoue la trace GPS avec une pause d'une seconde entre chaque point |
| `python main.py --data-dir data/test_errors` | Exécution sur des fichiers volontairement corrompus |
| `python main.py --data-dir data/demo_extended` | Données supplémentaires : sortie de zone, ré-entrée, freinage sur plusieurs échantillons |
| `python main.py --vehicle-id TRUCK-42` | Change l'identifiant du camion écrit dans le JSON (défaut : `TRUCK-DEMO`) |

Fichiers produits :

- `output/daily_score.json` : rapport agrégé de la journée, réécrit à chaque exécution. Chaque événement porte l'horodatage de son début (`timestamp`) et celui de son pic (`peak_timestamp`).
- `output/poc.log` : journal horodaté. Chaque exécution s'ajoute à la suite, après une ligne séparatrice datée.

## Tests

```bash
python -m unittest discover tests
```

Les tests écrivent dans un dossier temporaire : `output/` n'est pas modifié.

## Résultats attendus

### Exécution nominale (`python main.py`)

```
[INFO] #1 08:00:00 OUT (hors bounding box)
[INFO] #2 08:00:10 OUT (hors bounding box)
[INFO] #3 08:00:20 OUT (dans la bounding box, hors polygone) - écart : énoncé attend « IN (Bordure) »
[ALERT ZFE] #4 08:00:30 lat 45.7600 lon 4.8357 : entrée en zone à faibles émissions
[INFO] #5 08:00:40 IN (déjà en zone, pas de nouvelle alerte)
[ALERT SAFETY] 1678880005 freinage violent : acc_y = -3.45 m/s² (seuil -2.5)
```

- Une seule alerte ZFE, au point 4 (08:00:30).
- Un seul freinage violent, à l'horodatage 1678880005, pic de -3,45 m/s².
- Score du jour : 90 sur 100.
- Code de sortie : 0.

### Données corrompues (`--data-dir data/test_errors`)

Un point GPS sans `lat`, une valeur `abc` à la place de `acc_y` et une ligne CSV tronquée sont signalés par des `[WARNING]`, puis ignorés. Le programme va jusqu'au bout, sans trace d'erreur Python, et produit les mêmes alertes.

### Données étendues (`--data-dir data/demo_extended`)

Deux alertes ZFE (points 4 et 8), une sortie de zone (`[INFO ZFE]`, point 6), deux freinages violents, score de 80. Le second freinage dure trois échantillons : il commence à 1678880015 et son pic survient à 1678880017.

### Fichier absent

Si un fichier d'entrée manque, le moteur concerné affiche un `[ERROR]` et est ignoré ; l'autre moteur s'exécute. Le code de sortie vaut alors 1.

### Aucune mesure valide

Si le CSV ne contient aucune ligne exploitable, le moteur Safety affiche `[ERROR] Aucune mesure exploitable : score non calculé` et n'écrit aucun rapport. Le moteur ZFE s'exécute ; le code de sortie vaut 1.

## Choix techniques

| Choix | Raison |
|---|---|
| **Bounding box** avant tout calcul | Quatre comparaisons suffisent à écarter un point manifestement hors zone |
| **Ray casting** (point dans un polygone) | Verdict exact, en une seule boucle sur les arêtes du polygone |
| Alerte au passage de OUT à IN uniquement | Pas d'alerte répétée tant que le camion reste en zone |
| Seuil en **m/s²** sur `acc_y` (`< -2.5`) | Unité et seuil de l'Annexe 3 de l'énoncé |
| Pas de moyenne glissante | Elle ferait disparaître un pic isolé |
| Regroupement des échantillons consécutifs | À 100 Hz, un freinage dure plusieurs échantillons et ne doit compter qu'une fois |
| Horodatage décimal accepté | Le format réel à 100 Hz (1678880000.01) est lu sans perte |
| Pas de score sans mesure valide | Un capteur en panne ne produit jamais 100/100 |
| **Lecture en flux** du CSV (`csv.reader`) | Une seule ligne en mémoire à la fois, compatible avec la limite de 150 Mo |
| Agrégation locale (`daily_score.json`) | Environ 104 Mo de mesures brutes par jour restent sur la tablette ; moins de 1 Ko est à transmettre |
| Seuils et pénalités en constantes nommées | Règles ajustables sans toucher aux algorithmes |

Complexité : le traitement est linéaire. La boucle interne du ray casting dépend du nombre de sommets du polygone (fixe), pas du volume de données.

Score : `100 - 10 x nombre de freinages violents`, minimum 0. Cette grille est un paramètre du POC, pas une règle de l'assureur.

## Écarts assumés avec l'énoncé

- **Point GPS n° 3** : l'énoncé l'annonce « IN (Bordure) », le calcul le classe OUT. Il est dans la bounding box mais hors du polygone. L'écart est signalé dans le log, sans être corrigé.
- **Seuil** : le Dossier 4 parle de « 2.5G », l'Annexe 3 de 2,5 m/s². Le seuil retenu est -2,5 m/s² : avec 2,5 G (environ 24,5 m/s²), aucun événement ne serait détecté.

## Limites connues

- Les coordonnées (lat, lon) sont traitées comme des coordonnées planes, approximation acceptable à l'échelle d'une ZFE.
- La frontière du polygone n'a pas de marge de sécurité : un point exactement sur le bord peut être classé d'un côté ou de l'autre.
- Les fichiers de l'énoncé sont des extraits à un point par seconde, alors que la tablette mesure à 100 Hz.
- La trace GPS est chargée en entier (5 points) ; sur la tablette, les points arrivent un par un et sont traités au fil de l'eau.
- Le POC valide la logique des algorithmes, pas leur implémentation sur Android.
- Hors périmètre : interface HUD, gestion des comptes, envoi réel en 4G, cartographie européenne complète, détection des virages brusques (évolution future).

## Structure du dépôt

```
GML-Edge/
├── README.md
├── main.py                 point d'entrée : options, lancement des moteurs, récapitulatif
├── zfe_engine.py           moteur ZFE : bounding box, ray casting, machine d'état
├── safety_engine.py        moteur Safety : lecture en flux, seuils, score, JSON
├── data/
│   ├── lyon_polygon.json
│   ├── truck_gps.json
│   ├── accelerometer_data.csv
│   ├── demo_extended/      données supplémentaires
│   └── test_errors/        fichiers volontairement corrompus
├── output/
│   ├── daily_score.json
│   └── poc.log
└── tests/
    ├── test_zfe.py
    ├── test_safety.py
    └── test_main.py
```