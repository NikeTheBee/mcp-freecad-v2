# MCP FREECAD 2 🇫🇷

[![Tests](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml/badge.svg)](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml)
[![Licence : PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-lightgrey.svg)](LICENSE)
🇬🇧 [English version](README.md)

Concevez des pièces **mécaniques, aéro et robotiques** dans **FreeCAD** en **langage naturel**, via
n'importe quel client IA parlant **MCP** — avec une couche de fiabilité qui **mesure** le résultat
au lieu de croire la lecture qu'en fait l'IA.

> Décrivez l'intention (« une plaque 60×40×8 mm avec un trou débouchant Ø6 au centre ») ; l'IA
> modélise, enregistre vos exigences comme des contrôles, vérifie la géométrie et les exigences après
> chaque étape, sauvegarde automatiquement, et permet de revenir en arrière.

## Depuis la 2.1 (non publié)
- **Export vérifié** (`export`, nouvel outil) : STL ou 3MF pour l'impression, STEP, BREP ou IGES
  pour une autre CAO, écrit seulement pour une pièce que `check` valide. Le fichier est ensuite
  relu et comparé au solide (volume, dimensions, position, maillage fermé). Un fichier qui ne
  correspond pas n'est jamais livré, et ne remplace jamais un bon fichier.
- **Dimensions exactes sur les pièces courbes** : la boîte englobante rapide de FreeCAD lisait
  un loft de 30×20 comme 37,3×26,8, et une révolution de B-spline plus petite qu'elle n'est (une
  vraie collision passait alors inaperçue). Dimensions, exigences, plateau, exports et
  collisions utilisent maintenant la boîte exacte, dans les couches et dans la base.
- **Des trous bien mesurés sur de vraies pièces** : un trou débouchant dans la semelle d'une
  équerre en L, sous un lamage ou incliné passait pour « borgne » (comparé à la hauteur de toute
  la pièce), et un alésage stocké en B-spline (un STEP venu d'un autre CAO) n'était pas vu du
  tout. « Débouchant » veut maintenant dire ouvert aux deux bouts, jugé sur les faces de chaque
  extrémité (1 600 trous : moins d'une seconde). Un trou borgne fait par `pd_hole` (ou par le
  trou de la base dans un Body) part de la surface sous lui, pas du sommet de toute la pièce (sur
  une pièce à gradins il commençait dans le vide), et il est mesuré une fois fait : débouchant,
  profondeur. Sur un solide Part simple, le fraisage de la base ne coupait rien (son cône était
  sous la pièce) et le lamage se faisait sur la face du dessous : les deux sont maintenant
  dessus.
- **Révolution autour de l'axe nommé** : sur une esquisse du plan XZ, « autour de y » tournait
  autour de Z alors que la réponse disait « around Y-axis » (la lettre désignait les axes propres
  de l'esquisse). Révolution, rainure et hélice prennent maintenant les axes de la pièce, comme
  les réseaux ; la normale de l'esquisse est refusée.
- **Une fonction laissée hors de la pièce donne FAIL** : un Body dont la Tip s'arrête avant sa
  dernière fonction, ou avec une fonction supprimée, passait OK (un solide valide, sans le trou).
- **Les fichiers arrivent dans le projet** : un fichier nommé sans dossier (`plan.dxf`,
  `piece.stl`) atterrissait parmi le code du serveur ; il va maintenant dans le dossier du projet.
- **Pièces lourdes** (mesuré sur une plaque percée de 1 600 trous) :
  - épaisseur de paroi : 73 s → 0,7 s, et désormais exacte (les rayons rencontrent les vraies
    surfaces, sans maillage) ;
  - `printability` : 129 s → 1,6 s ; `analyze` : 132 s → 1,7 s ; `render_views` : 48 s → 13 s ;
  - détection des trous : 2,7 s → 0,2 s (même résultat, sans comparer chaque paire) ;
  - `check` ne refait l'analyse OCCT, lente, qu'une fois par version de la forme ;
  - les outils d'analyse attendent FreeCAD jusqu'à 5 minutes au lieu de 2, et un calcul
    encore en cours est expliqué clairement au lieu d'un « no response » brut.
- **Une entrée invalide reçoit une raison, pas une mauvaise réponse** (les 17 outils passés en
  revue) : les exigences mal formées sont refusées (une faute de frappe n'affaiblit plus le
  contrat), une exigence se remplace ou se retire (`replaces="S3"`), et un trou qui ne
  coupe rien ou casse la pièce est annulé.
- `fem_static` refuse d'emblée une taille de maille irréaliste (Gmsh moulinait d'abord) et indique
  la taille qui convient.
- **Une opération longue ne passe plus pour un plantage** : au-delà de 35 s, la base annonçait
  « FreeCAD a planté » alors que FreeCAD headless calculait encore. Elle rend maintenant le
  résultat (ou, au-delà de 2 minutes, un identifiant de tâche à interroger). Un vrai plantage
  pendant un appel des couches est signalé comme tel, avec le moyen de s'en remettre (`recover`).
- **Un FreeCAD bloqué peut être redémarré par l'assistant** (`restart_freecad`, aussi sans
  interface) : il enregistre ce qui peut l'être sans risque, quitte, et le lanceur en démarre un
  neuf. Avant, seul un arrêt manuel du processus en venait à bout.
- **Assemblages mesurés pièce par pièce** : la nomenclature compte une seule fois les pièces
  identiques quelle que soit leur orientation ; trous, porte-à-faux et imprimabilité jugent
  chaque pièce seule ; un plan ne met jamais de cote d'encombrement sur une vue iso (une
  longueur projetée n'est pas une dimension).
- **Retour arrière et récupération préservent le travail** : un checkpoint restauré ou une copie
  récupérée se rouvre sous le nom du document, en copie de travail (un enregistrement n'écrase
  plus le checkpoint), et après un plantage `recover` ramène le retour arrière choisi.

## Nouveautés de la 2.1
- **Calcul de résistance** (`fem_static`, CalculiX) : déplacement, contrainte de von Mises et
  coefficient de sécurité, avec un verdict. Une poutre encastrée tombe à 0,3 % de F·L³/3EI.
- **Images sans interface** (`render_views`) : vues face, dessus, droite et iso ombrées, en une
  image.
- **Plusieurs projets en parallèle** (`project_init` n'écrase jamais, `project_switch`) et
  **recettes vérifiées** (`recipe`) : une conception n'est gardée que si son contrat est OK.
- **Plus sûr par construction** :
  - FreeCAD prouve qu'il connaît la clé avant tout envoi (défi HMAC) ;
  - les outils CAM (G-code machine) sont coupés sauf activation explicite ;
  - les outils inutilisables sous Windows sont masqués (environ 2 200 tokens économisés par
    session).
- **Base mise à jour en v8.3.0 de blwfish**, nos corrections conservées. Aussi corrigés dans la
  base :
  - congés et chanfreins sans interface, avec arêtes nommées ;
  - tous les chanfreins hors Body ;
  - suivi des crashs sous Windows ;
  - messages clairs au lieu d'erreurs Python sans interface, trouvés en passant en revue les 190
    opérations.
- **CI Windows** : à chaque envoi, installation du FreeCAD 1.1.1 officiel et passage des 36 tests,
  aucun ignoré.

## Nouveautés de la 2.0
- **Le serveur de base fait partie du projet.** blwfish/freecad-mcp v8.3.0 est intégré dans
  `server/freecad-mcp/` (git subtree) : corrigé ici, et toujours capable de récupérer les mises à
  jour d'origine (`python install/bootstrap.py --update-base`). Corrigés : trous dans un Body
  PartDesign, verdict « entièrement contrainte » des esquisses, réponses trop grosses qui bloquaient
  2 minutes ([`CHANGES-NikeTheBee.md`](server/freecad-mcp/CHANGES-NikeTheBee.md)).
- **Couche de fiabilité** (`freecad-layers`, 17 outils) : verdicts OK/FAIL/UNKNOWN, contrat de
  conception, analyse de pièce, **vrai calcul de résistance (CalculiX)**, checkpoints,
  sauvegarde/récupération, relance supervisée de FreeCAD, anti-usurpation du port, et FreeCAD
  prouve son identité avant toute commande.
- **Nouvelle licence** : PolyForm Noncommercial (libre d'utiliser et modifier, interdit de vendre).
- La version 1 reste disponible, inchangée :
  [NikeTheBee/mcp-freecad](https://github.com/NikeTheBee/mcp-freecad).

## Objectif : l'IA locale d'abord
Le but final est la CAO pilotée par des modèles d'IA **locaux**. L'architecture repose sur **MCP**,
un protocole ouvert : n'importe quel agent MCP peut la piloter. Les modèles frontier (ex. Claude
Code) servent de client de référence en attendant.

## Fonctionnement
```
Vous (langage naturel)
  → client IA (Claude Code / Claude Desktop / tout client MCP)
    ├─ `freecad`         lanceur (démarre/surveille FreeCAD, refuse un port usurpé)
    │                      → bridge freecad-mcp (blwfish v8.3.0, intégré + corrigé, ~39 outils)
    └─ `freecad-layers`  18 outils de fiabilité : project_status · project_init · project_switch · recipe ·
                          require · check · analyze · fem_static · render_views ·
                          check_assembly · printability · export · record_step · checkpoint ·
                          recover · bill_of_materials · drawing_sheet · pd_hole
         → FreeCAD 1.1 headless sur localhost:23456 (jeton) + ateliers greffés
```
- **Le code décide, pas l'IA.** `check` répond `OK`, `FAIL` ou `UNKNOWN` (un contrôle qui n'a pas pu
  tourner n'est jamais présenté comme OK), avec les mesures du noyau et vos exigences (encombrement,
  trous et positions, masse, épaisseur de paroi, interférences).
- **Rien ne se perd.** Mémoire projet robuste aux sessions et aux écritures simultanées, copie de
  secours après chaque étape validée, checkpoints rechargés depuis le disque, FreeCAD relancé
  immédiatement après un crash.
- **Sobre en tokens.** Les outils de fiabilité coûtent ~3 300 tokens de schéma ; réponses en texte
  court ; pas de capture d'écran par défaut.

## Installation (une commande, Windows)
```
git clone https://github.com/NikeTheBee/mcp-freecad-v2 "MCP FREECAD" && cd "MCP FREECAD"
python install/bootstrap.py --with-grafts
```
Prérequis : **FreeCAD 1.1.x**, **Python 3.10+**, **git**, un client MCP. Le serveur de base est livré
dans le dépôt ; l'installeur fige chaque autre composant tiers sur un commit, crée un environnement Python dédié (`~/.freecad-mcp/venv`, rien dans
votre Python système), enregistre les deux serveurs MCP et lance les tests. Il s'arrête à la
première erreur et n'écrase jamais une configuration Claude Desktop illisible. Relançable sans risque.

## Vérifier
```
python install/run_all_tests.py
```
~36 tests : les pièges de l'audit (verdicts qui passaient sur des pièces fausses), couches, greffes,
lanceur (authentification, relance après crash, refus d'un port usurpé) et boucles MCP complètes —
sur une instance FreeCAD **isolée** (port 23499), jamais sur votre instance réelle. Un test ignoré
s'affiche SKIP, jamais comme réussi. La base intégrée garde aussi ses ~2 100 tests unitaires
(`pytest tests/unit` dans `server/freecad-mcp`).

## Capacités et maturité
**Stable** = tests d'exactitude (valeurs connues + pièges). **Bêta** = testé de bout en bout, limites
documentées. **Expérimental** = marche sur les cas testés seulement.

| Domaine | Capacité | Maturité |
|---|---|---|
| Fiabilité | Verdicts géométriques (recalcul périmé, auto-intersection, interférence), contrat de conception, analyse de pièce (trous, parois), mémoire projet, checkpoints, sauvegarde auto | **Stable** |
| Organisation | Plusieurs projets en parallèle (`project_init` n'écrase jamais, `project_switch`) ; recettes vérifiées (une conception n'est gardée que si son contrat est OK) | **Bêta** |
| Modélisation | PartDesign (esquisse, Pad/Pocket, vrais trous dans le Body), variantes par feuille de calcul | **Stable** |
| Fabrication | Export STL/3MF/STEP/IGES seulement après `check` OK, fichier relu et comparé au solide · imprimabilité FDM (plateau, parois, porte-à-faux) · réparation de maillage | **Stable** |
| Documentation | Nomenclature (vraies pièces, liens, CSV) | **Stable** |
| Contrôle visuel | `render_views` : vues face/dessus/droite/iso ombrées en une image, sans interface, à la demande | **Bêta** |
| Plans 2D | Vues TechDraw, échelle ISO auto, contrôle de mise en page, cotes d'encombrement → DXF | **Bêta** (PDF via l'interface) |
| Mécanismes | Assemblages (placement), engrenages droits, ajustements, cinématique (bielle-manivelle, quatre barres) | **Bêta** |
| Calcul de structure | `fem_static` : CalculiX statique linéaire, déplacement, von Mises, coefficient de sécurité, contrôle de convergence (F·L³/3EI à 0,3 % près) | **Bêta** (élastique linéaire) |
| Analyse | Dimensionnement drone · stabilité fusée (Barrowman) | **Expérimental** |
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
[PolyForm Noncommercial 1.0.0](LICENSE) © 2026 NikeTheBee — chacun est libre d'**utiliser,
modifier et partager** pour tout usage non commercial ; **le vendre ou tout usage commercial**
demande une autorisation écrite. Gardez la ligne `Required Notice` de [`NOTICE`](NOTICE) et les
crédits de [`CREDITS.md`](CREDITS.md). Exception : `server/freecad-mcp/` (blwfish/freecad-mcp,
modifié) reste sous **LGPL-2.1-or-later**. FreeCAD et les ateliers greffés gardent leurs propres
licences (LGPL-2.1, non inclus ici).
