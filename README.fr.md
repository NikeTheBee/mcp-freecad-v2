# MCP FREECAD 2

[![Tests](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml/badge.svg)](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml)
[![Licence : PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-lightgrey.svg)](LICENSE)
[![FreeCAD 1.1](https://img.shields.io/badge/FreeCAD-1.1.x-red.svg)](https://www.freecad.org)

[English version](README.md)

Un atelier d'ingénierie qu'on pilote en langage courant. FreeCAD fait la CAO paramétrique, des
solveurs libres et gratuits font la physique, et un serveur MCP relie les deux à n'importe quel
client IA. Chaque réponse est une mesure assortie d'un verdict (OK, FAIL ou UNKNOWN), pas l'avis du
modèle sur son propre travail.

Vous décrivez une pièce ou un projet complet (« une plaque de 60 x 40 x 8 mm percée d'un trou
débouchant de 6 mm au centre », « un robot gyropode qui porte 500 g »). L'agent le modélise, note
vos exigences sous forme de contrôles, vérifie la géométrie après chaque étape, lance les
simulations et s'arrête aux fichiers de fabrication. La fabrication et l'essai réel restent les
vôtres ; vos mesures servent ensuite à recaler les modèles.

## Ce qui est couvert

| Domaine | Contenu |
|---|---|
| CAO | PartDesign, esquisses, vrais perçages, assemblages et liaisons, variantes par tableur, pièces normalisées (vis, écrous, rondelles ISO), plans TechDraw vers DXF, nomenclature et notice de montage |
| Contrôles | verdicts géométriques, contrat de conception (cotes, perçages, masse, paroi, interférences), imprimabilité, fabricabilité, points de reprise et récupération après plantage |
| Structures | CalculiX en statique, modes propres, flambage, thermique et thermomécanique ; fatigue ; stratifiés composites ; propagation de fissure |
| Fluides | SU2, Elmer, OpenFOAM 12 (en série ou en MPI sur vos cœurs), profils, ailes, tuyères, combustion |
| Aéro et espace | vol de fusée (RocketPy, OpenRocket), flottement et divergence, POGO, OpenVSP, orbites et GMAT, optimisation de trajectoire (dymos), cycles de turboréacteur (pyCycle) |
| Électronique | SPICE, batteries, aimants, dimensionnement des câbles, bilan de puissance, pistes / vias / impédance de circuit imprimé, ERC de netlist, faisceaux WireViz, KiCad 10 (DRC, Gerber, STEP) |
| Robotique | dynamique multicorps (MuJoCo, Pinocchio), commande, capteurs (bruit d'Allan, ADC, Kalman), moteurs à courant continu, ArduPilot SITL, Gazebo, ROS 2 |
| IA et microprogramme | réseaux de neurones de pilotage et de décision (apprentissage supervisé et par renforcement, export ONNX), microprogramme STM32 dans Renode, et ce microprogramme qui pilote le robot simulé en boucle fermée |
| Marin, sol | hydrostatique, propulseurs, coques sous pression, joints toriques, eaux souterraines et sols (OpenGeoSys) |

L'atelier compte 29 adaptateurs et environ 170 cas de référence. Un type de calcul ne tourne qu'une
fois ses cas de référence validés (bancs d'essai de type NAFEMS, formules exactes, valeurs
publiées). Il est revérifié dès que son code ou son environnement verrouillé change.

## Comment tout s'articule

```
vous, en langage courant
  -> tout client MCP (harness en ligne de commande, Cursor, VS Code, Codex, Gemini CLI, LM Studio, Continue...)
     |- freecad          le lanceur surveille un FreeCAD sans interface (local uniquement, jeton)
     |                   et sert les outils de modélisation de la base freecad-mcp intégrée
     '- freecad-layers   contrôles, contrat, export, simulate, study, skills
          -> le lanceur de l'atelier démarre chaque solveur dans son propre environnement verrouillé
```

Deux choses gardent la consommation de tokens basse. Les résultats sont des verdicts courts avec
quelques chiffres ; les journaux complets restent sur le disque. Les schémas d'outils sont
compacts : l'ensemble coûte environ 15 000 tokens au démarrage d'une session, et un test fait
échouer la suite au-delà de 15 500. Les connaissances métier sont dans `skills/` et ne sont lues
qu'au besoin, par un outil `skill`, des ressources MCP ou un prompt, de la même façon dans tous les
clients.

## Installation (Windows)

```
git clone https://github.com/NikeTheBee/mcp-freecad-v2 "MCP FREECAD" && cd "MCP FREECAD"
python install/bootstrap.py --with-grafts
powershell -ExecutionPolicy Bypass -File install\install.ps1 -Home D:\Atelier -Packs tout -Scan
```

`bootstrap.py` prépare la partie MCP : un environnement Python dédié, les deux serveurs
enregistrés, puis les tests. Il s'arrête à la première erreur et n'écrase jamais une
configuration client qu'il ne sait pas relire.

`install.ps1` construit l'atelier dans le dossier choisi (`-Home`), avec des téléchargements
épinglés et vérifiés par SHA256, et des environnements pixi verrouillés. Quelques comportements à
connaître :

- `-Packs` choisit les domaines : socle, fluides, fluides-plus, opt, air, fusee, espace, terre,
  soussol, marin, elec, embarque, fab, outillage, ros, ia, ou `tout` pour tous.
- `-Scan` demande d'abord votre accord, puis relève le processeur, la mémoire, la carte graphique
  et le disque libre. Les téléchargements et le profil de calcul par défaut en découlent : par
  exemple, la version CUDA de l'environnement IA n'est ajoutée que pour une carte NVIDIA avec un
  pilote assez récent. Rien ne quitte la machine.
- Avant tout téléchargement, l'installeur cherche ce qui est déjà présent, sur tous les disques.
  Une copie de la même version est proposée à la réutilisation ; une autre version est seulement
  signalée. Les paquets communs à plusieurs environnements ne sont stockés qu'une fois.
- Avant de télécharger, il affiche la taille totale et demande confirmation.

Pour un autre client que celui trouvé par le bootstrap :
`python install/mcp_clients.py --client cursor --write`. Les autres clients sont vscode, codex,
gemini, lmstudio, continue, windsurf et desktop-harness. Sans `--write`, la configuration est
seulement affichée.

## Calcul : CPU, GPU ou les deux

Toute simulation accepte `device` : `cpu`, `gpu`, ou `both` (le réseau s'entraîne sur le GPU
pendant que la physique tourne sur les cœurs). Le choix par défaut vient du scan matériel ; sans
scan, tout tourne sur le CPU. OpenFOAM prend `cpus` pour le nombre de rangs MPI, et CalculiX
utilise les threads du profil.

## Règles de sécurité appliquées par le code

- FreeCAD et chaque solveur n'écoutent que sur la machine locale ; un processus qui ouvre autre
  chose est arrêté.
- Aucun G-code machine n'est jamais écrit. Le microprogramme tourne dans un émulateur, et une
  politique entraînée sort en fichier ONNX. Flasher une carte, commander des pièces ou faire
  bouger une machine reste une action humaine.
- Les fichiers fournis qui pourraient exécuter du code sont refusés : dictionnaires OpenFOAM qui
  compilent du C++, modèles sérialisés par pickle, inclusions MJCF, extensions KiCad.
- La sortie des outils est traitée comme une donnée, jamais comme une consigne pour l'agent.

Le détail est dans [SECURITY.md](SECURITY.md) et [docs/AUDIT_CYBER_2026-11-21.md](docs/AUDIT_CYBER_2026-11-21.md).

## Tests

```
python install/run_all_tests.py
```

La suite compte 58 tests. Les tests de bout en bout utilisent un FreeCAD isolé (port 23499,
dossier privé), jamais celui en service. Ils couvrent :
- les pièges relevés à l'audit d'octobre ;
- les couches et le lanceur ;
- des boucles MCP complètes depuis un client nu ;
- les cas de référence des adaptateurs rapides ;
- le portail cyber et le budget de tokens.

Un test sauté s'affiche SKIP, jamais PASS. La base intégrée garde ses propres tests unitaires
(`pytest tests/unit` dans `server/freecad-mcp`).

## Documentation

- [AGENTS.md](AGENTS.md) : les règles que suit tout agent, et la carte du dépôt (en anglais).
- [docs/FEUILLE_DE_ROUTE_ATELIER.md](docs/FEUILLE_DE_ROUTE_ATELIER.md) : feuille de route de l'atelier, outils et état.
- [SECURITY.md](SECURITY.md) : modèle de confiance, chaîne d'approvisionnement, ce qui n'est pas protégé.
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) : risques de version et liste de contrôle des mises à jour.
- [CHANGELOG.md](CHANGELOG.md) : historique avant l'atelier (en anglais).
- `skills/<domaine>/SKILL.md` : paramètres, limites et validation de chaque domaine.

## La suite

Une version finale complète est prévue autour de CATIA V5 : la même tuyauterie entre une IA et les
outils de CAO et de simulation, construite cette fois autour de CATIA. Le but est de matérialiser
l'imagination. Elle s'adresse aux jeunes débutants qui ont des idées et un peu de temps, et qui
veulent fabriquer pour le plaisir. Tout ce qu'il faut est déjà à portée de main : cartes Arduino,
ESP32 et Raspberry Pi, moteurs et servomoteurs, capteurs en tout genre, cartes de commande de
moteurs, et le reste, sans limite à ce qu'on peut imaginer. CATIA V5 est un logiciel commercial
de Dassault Systèmes : cette version demandera donc une licence CATIA, et l'IA le pilotera par
l'interface d'automatisation de CATIA sous Windows.

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE), © 2026 NikeTheBee. Libre d'utilisation, de modification et
de partage pour tout usage non commercial ; la vente ou tout usage commercial demande une
autorisation écrite. Conservez la `Required Notice` de [NOTICE](NOTICE) et les crédits de
[CREDITS.md](CREDITS.md).

Deux exceptions :
- `server/freecad-mcp/` (blwfish/freecad-mcp, modifié) reste sous LGPL-2.1 ou ultérieure.
- FreeCAD, les ateliers greffés et chaque outil de l'atelier gardent leur propre licence. Ils sont
  téléchargés par l'installeur, pas fournis ici.
