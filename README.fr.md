# MCP FREECAD 🇫🇷

[![Tests](https://github.com/NikeTheBee/mcp-freecad/actions/workflows/tests.yml/badge.svg)](https://github.com/NikeTheBee/mcp-freecad/actions/workflows/tests.yml)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC_BY--NC_4.0-lightgrey.svg)](LICENSE)
🇬🇧 [English version](README.md)

Concevez des pièces **mécaniques, aéro et robotiques** dans **FreeCAD** en **langage naturel**, via
n'importe quel client IA parlant **MCP** — avec une couche de fiabilité qui **mesure** le résultat
au lieu de croire la lecture qu'en fait l'IA.

> Décrivez l'intention (« une plaque 60×40×8 mm avec un trou débouchant Ø6 au centre ») ; l'IA
> modélise, enregistre vos exigences comme des contrôles, vérifie la géométrie et les exigences après
> chaque étape, sauvegarde automatiquement, et permet de revenir en arrière.

## Objectif : l'IA locale d'abord
Le but final est la CAO pilotée par des modèles d'IA **locaux**. L'architecture repose sur **MCP**,
un protocole ouvert : n'importe quel agent MCP peut la piloter. Les modèles frontier (ex. Claude
Code) servent de client de référence en attendant.

## Fonctionnement
```
Vous (langage naturel)
  → client IA (Claude Code / Claude Desktop / tout client MCP)
    ├─ `freecad`         lanceur (démarre/surveille FreeCAD, refuse un port usurpé)
    │                      → bridge blwfish/freecad-mcp (figé en v8.2.3, non modifié, ~39 outils)
    └─ `freecad-layers`  13 outils de fiabilité : project_status · require · check · analyze ·
                          check_assembly · printability · record_step · checkpoint · recover ·
                          bill_of_materials · drawing_sheet · pd_hole · project_init
         → FreeCAD 1.1 headless sur localhost:23456 (jeton) + ateliers greffés
```
- **Le code décide, pas l'IA.** `check` répond `OK`, `FAIL` ou `UNKNOWN` (un contrôle qui n'a pas pu
  tourner n'est jamais présenté comme OK), avec les mesures du noyau et vos exigences (encombrement,
  trous et positions, masse, épaisseur de paroi, interférences).
- **Rien ne se perd.** Mémoire projet robuste aux sessions et aux écritures simultanées, copie de
  secours après chaque étape validée, checkpoints rechargés depuis le disque, FreeCAD relancé
  immédiatement après un crash.
- **Sobre en tokens.** Les outils de fiabilité coûtent ~2 000 tokens de schéma ; réponses en texte
  court ; pas de capture d'écran par défaut.

## Installation (une commande, Windows)
```
git clone https://github.com/NikeTheBee/mcp-freecad "MCP FREECAD" && cd "MCP FREECAD"
python install/bootstrap.py --with-grafts
```
Prérequis : **FreeCAD 1.1.x**, **Python 3.10+**, **git**, un client MCP. L'installeur fige chaque
composant tiers sur un commit, crée un environnement Python dédié (`~/.freecad-mcp/venv`, rien dans
votre Python système), enregistre les deux serveurs MCP et lance les tests. Il s'arrête à la
première erreur et n'écrase jamais une configuration Claude Desktop illisible. Relançable sans risque.

## Vérifier
```
python install/run_all_tests.py
```
~33 tests : les pièges de l'audit (verdicts qui passaient sur des pièces fausses), couches, greffes,
lanceur (authentification, relance après crash, refus d'un port usurpé) et boucles MCP complètes —
sur une instance FreeCAD **isolée** (port 23499), jamais sur votre instance réelle. Un test ignoré
s'affiche SKIP, jamais comme réussi.

## Capacités et maturité
**Stable** = tests d'exactitude (valeurs connues + pièges). **Bêta** = testé de bout en bout, limites
documentées. **Expérimental** = marche sur les cas testés seulement.

| Domaine | Capacité | Maturité |
|---|---|---|
| Fiabilité | Verdicts géométriques (recalcul périmé, auto-intersection, interférence), contrat de conception, analyse de pièce (trous, parois), mémoire projet, checkpoints, sauvegarde auto | **Stable** |
| Modélisation | PartDesign (esquisse, Pad/Pocket, vrais trous dans le Body), variantes par feuille de calcul | **Stable** |
| Fabrication | STL conditionné à l'étanchéité + imprimabilité FDM · STEP/IGES · réparation de maillage | **Stable** |
| Documentation | Nomenclature (vraies pièces, liens, CSV) | **Stable** |
| Plans 2D | Vues TechDraw, échelle ISO auto, contrôle de mise en page, cotes d'encombrement → DXF | **Bêta** (PDF via l'interface) |
| Mécanismes | Assemblages (placement), engrenages droits, ajustements, cinématique (bielle-manivelle, quatre barres) | **Bêta** |
| Analyse | FEM CalculiX (mise en données ; résolution non automatisée) · dimensionnement drone · stabilité fusée (Barrowman) | **Expérimental** |
| Greffes | Rocket · AirPlaneDesign (cœur headless) · CROSS URDF/xacro + package ROS 2 | **Expérimental** |
| CAM / G-code | — | **Retiré** : l'ancienne couche produisait du G-code dangereux. Exporter en STEP et programmer dans un outil de FAO maîtrisé. |
| Cinématique inverse | — | **Retirée** : nécessite Pinocchio, indisponible sous Windows. |
| CFD | — | Non inclus : export pour un cas OpenFOAM externe (voir `skill-drone`). |

Connaissance métier : [`skills/`](skills), chargée à la demande.

## Documentation
- [`docs/AUDIT_2026-10-11.md`](docs/AUDIT_2026-10-11.md) — audit complet, résultats, plan de réparation.
- [`SECURITY.md`](SECURITY.md) · [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) ·
  [`docs/CAHIER_DES_CHARGES.md`](docs/CAHIER_DES_CHARGES.md) (spécification d'origine).

## Licence
[CC BY-NC 4.0](LICENSE) © 2026 NikeTheBee — chacun est libre de **télécharger, modifier et
partager**, à deux conditions : **citation obligatoire** (créditer NikeTheBee ET les projets tiers
de [`CREDITS.md`](CREDITS.md)), et **pas d'usage commercial** sans autorisation écrite préalable.
Résumé lisible : [`NOTICE`](NOTICE). FreeCAD et les workbenches greffés gardent leurs propres
licences (LGPL-2.1, non vendorisés ici).
