# Analyse de manques — outils / fonctionnalités vs besoins exprimés

> Revue du 2026-07-04, croisant l'outillage réel exposé par le serveur MCP,
> les besoins du cahier des charges (§4) et une veille en ligne (FreeCAD 1.1/1.2,
> workbenches, autres serveurs MCP). But : lister ce qui **manque encore** et
> ce qui est **faisable** pour s'en rapprocher. Complète `docs/ROADMAP.md`.

> **MàJ 2026-07-04 (soir)** : les manques G1–G6 ont été **implémentés et testés**
> (suite 30/30). Statut par item ci-dessous. Restent hors périmètre : G7 (rendu
> réaliste, contraire à « texte d'abord ») et les non-manques déjà différés.

## Statut d'implémentation (après action)

| # | Manque | Statut | Livré |
|---|---|---|---|
| G1 | Plans 2D TechDraw | ✅ **Fait** | `freecad_layers/drawing.py`, `skill-techdraw`, `techdraw_test.py` (DXF headless ; PDF/SVG = GUI, documenté) |
| G2 | Cinématique linkage | ✅ **Fait** | `kinematics.py` (dyad/slider-crank/four-bar + solveur natif), `skill-kinematics`, `kinematics_test.py` (pince à 4.52°/mm) |
| G3 | CAM / G-code | ✅ **Fait** (sur 1.1) | `cam.py` (job+perçage+post grbl), `skill-cam` réécrit, `cam_test.py` — décision « attendre 1.2 » **inversée** |
| G4 | IK robotique | ✅ **Fait** (exposée) | `robotics.py` (IK CROSS/Pinocchio + `ik_available()`), `skill-robotics-ros` MàJ, `ik_exposure_test.py` |
| G5 | Fasteners | ✅ **Fait** (greffe) | Fasteners WB épinglé `v0.5.39` dans `bootstrap.py` (on-demand), `skill-fasteners-bom` |
| G6 | Nomenclature/BOM | ✅ **Fait** | `bom.py` (groupage + CSV), `skill-fasteners-bom`, `bom_test.py` |
| G7 | Rendu réaliste | ⏸️ Volontairement non fait | contraire à la règle « texte d'abord » (§8) |

Toutes les couches ci-dessus sont **secure by design** : écritures fichier via les gates partagés
`safe_out_path`/`safe_under` (extension imposée, pas d'écrasement silencieux, anti-traversal),
et chaque test exerce les chemins de refus, pas seulement le cas nominal.

---

### (Analyse initiale conservée ci-dessous pour traçabilité)

## Méthode
- Inventaire des dispatchers réellement exposés (handler AICopilot) : part / partdesign /
  sketch / draft / boolean / transform / measurement / mesh / cam / spatial / spreadsheet /
  fixture / macro / view / verification / introspection + `execute_python`.
- Comparaison au périmètre §4 (modélisation, robotique, aéro, méca, fabrication, transverse).
- Signal terrain : le **test réel « pince »** (`A:\test 1 modelelisation`) exigeait
  des **plans 2D PDF** et une **simulation cinématique** — deux choses non outillées.

## Tableau des manques (priorité décroissante)

| # | Besoin (CDC) | État actuel | Ce qui manque | Faisable ? (veille) | Prio |
|---|---|---|---|---|---|
| G1 | Plans 2D cotés (§4.6 ; test pince « plan 2D pdf ») | **Aucun** outil TechDraw | Générer vues + cotes + export PDF/SVG/DXF | **Oui, scriptable headless** via `freecadcmd` (TechDraw API : pages, vues, dimensions, export) | 🔴 Haute |
| G2 | Cinématique d'assemblage / FK (§4.2, §4.4 ; pince) | Assemblage **placement seul**, joints « laissés au GUI » (R8) | Joints natifs + animation via solveur | **Oui** : OndselSolver + classe `Joint` scriptables en FreeCAD 1.0/1.1 | 🔴 Haute |
| G3 | CAM / G-code (§4.5) | **Différé à 1.2** (skill-cam = stub) | Jobs/opérations/post-proc sur **1.1** | **Oui, et 1.1 est le bon socle** : la 1.2 a une **régression** des post-processeurs custom (#26006). Décision à revoir. | 🟠 Moyenne-haute |
| G4 | Cinématique inverse IK (§4.2) | Non documenté | Exposer un résolveur IK | **Déjà présent** : CROSS embarque `ik.py` + wrapper **Pinocchio** — à surfacer dans le skill | 🟠 Moyenne |
| G5 | Visserie / filetages normalisés (§4.1 ; pince M5 + circlips) | `PartDesign::Hole` basique | Bibliothèque vis/écrous/filets ISO | **Oui** : Fasteners WB (`shaise`, v0.5.39/2025), `ScrewMaker` utilisable comme module Python | 🟠 Moyenne |
| G6 | Nomenclature / BOM (test pince) | Aucun | Extraire une nomenclature d'assemblage (qté, réf) | **Oui** : trivial via parcours des objets + spreadsheet/CSV | 🟡 Basse-moy. |
| G7 | Rendu réaliste / vues iso soignées (§4.6) | Screenshot offscreen basique | Rendu qualité (matériaux, ombres) | Possible (WB Render / sandraschi) mais **coûteux en tokens** — philosophie « texte d'abord » l'assume | 🟢 Basse |

## Détails & preuves

### G1 — TechDraw (plans 2D) — le plus gros trou fonctionnel
Le test pince demandait explicitement « un plan 2D au format pdf » par pièce. TechDraw est
**pilotable en Python et headless** (`FreeCADCmd`) : création de page (`TechDraw::DrawPage`),
vues projetées (`DrawViewPart`), cotes automatiques, export PDF/SVG/DXF. C'est le chaînon manquant
entre « modèle 3D » et « livrable atelier ». → candidat n°1 pour un `skill-techdraw` + helper.

### G2 — Joints & simulation cinématique
Pour la pince, j'ai dû **recalculer la cinématique à la main** (extraction des alésages + solveur
maison) faute de joints natifs exposés. Or FreeCAD 1.0/1.1 intègre l'**OndselSolver** avec une
classe `Joint` (Fixed, Revolute, Cylindrical, Slider, Ball ; transmissions RackPinion/Screw/Gears/
Belt) scriptable. Un `skill-kinematics` + couche `freecad_layers/kinematics.py` (poser un joint,
balayer un angle/translation, sortir la loi entrée/sortie) rendrait §4.2/§4.4 **natifs** au lieu
d'ad hoc. NB : reste plus fragile headless que le placement — à valider par test, comme R8 le prévoit.

### G3 — CAM sur 1.1 (correction d'une décision)
`docs/ROADMAP` et `skill-cam` disent « attendre 1.2 ». La veille **inverse ce constat** : le CAM 1.1
génère du G-code en Python (post-processeurs multi-contrôleurs), et la **1.2 casse** les post-proc
custom (issue #26006). Il existe même `ocp-freecad-cam` (API fluide, expérimentale). → viser un CAM
**basique sur 1.1** (job + perçage/contour/poche + post grbl) est plus réaliste qu'attendre 1.2.

### G4 — IK déjà là, non exposée
`addons/freecad.cross/freecad/cross/ik.py` + `solver_wrappers/pinocchio.py` : la cinématique inverse
est **présente via CROSS** mais absente du `skill-robotics-ros`. Gain immédiat = documentation +
petit wrapper, sans nouvelle dépendance lourde (Pinocchio requis côté ROS).

### G5 — Fasteners / filetages
Le `PartDesign::Hole` couvre counterbore/countersink/threaded « cosmétique ». Pour de la vraie
visserie normalisée (ISO M5 du sujet pince, circlips), le **Fasteners WB** (`shaise`) expose
`screw_maker.ScrewMaker` comme module Python. Greffe optionnelle (comme Rocket/CROSS), à épingler.

### G6 — Nomenclature / BOM
Aucun outil ne produit la liste des pièces (le sujet pince fournissait une « Nomenclature »).
Un helper qui parcourt l'assemblage → CSV/spreadsheet (réf, désignation, quantité, matériau) est
peu coûteux et boucle la boucle « conception → dossier de fabrication ».

## Non-manques (différés à juste titre)
- **CFD interne sérieux** : hors périmètre (§12), repli OpenFOAM externe déjà documenté (skill-cfd).
- **Simulation dynamique multicorps** : externalisée vers Gazebo (pipeline ROS).
- **Génération image→3D** : optionnelle, GPU (§15 D3 = non).
- **Rendu réaliste** : contraire à la règle « texte d'abord » sauf demande explicite.

## Recommandation de priorisation
1. **G1 TechDraw** (livrable atelier manquant, 100 % scriptable) — `skill-techdraw` + helper + test.
2. **G4 IK** (déjà présent, juste à exposer) — quick win.
3. **G2 Joints/cinématique native** (remplace l'ad hoc, valide §4.2/§4.4) — couche + test.
4. **G3 CAM 1.1** (revoir la décision « attendre 1.2 ») — CAM basique + post grbl.
5. **G5 Fasteners** (greffe optionnelle) puis **G6 BOM** (helper léger).

## Sources
- Assembly / OndselSolver joints scriptables : https://deepwiki.com/FreeCAD/FreeCAD/3.7.1-assembly-objects-and-joints · https://www.ondsel.com/blog/assembly-workbench-preview/
- TechDraw scripting headless : https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/TechDraw_Workbench.md · https://github.com/FreeCAD/FreeCAD/issues/5710
- CAM scripting + régression 1.2 : https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/CAM_scripting.md · https://github.com/FreeCAD/FreeCAD/issues/26006 · https://pypi.org/project/ocp-freecad-cam/
- RobotCAD / IK : https://github.com/FreeCADNexus/freecad.robotcad
- Fasteners WB (ScrewMaker) : https://github.com/shaise/FreeCAD_FastenersWB
