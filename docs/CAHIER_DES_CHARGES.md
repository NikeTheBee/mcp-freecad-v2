# CAHIER DES CHARGES — Pilotage IA de FreeCAD pour conception mécanique & robotique

> Projet : système permettant de concevoir en **langage naturel** (zéro code manuel) des projets mécaniques et robotiques (train, fusée, drone, robots) dans **FreeCAD 1.1.1**, via une IA (Claude Code / Claude Desktop et tout client MCP), avec **coût de tokens minimal** et **publication publique GitHub**.
> Version 1.0 · Statut : spécification initiale validée.

---

## 0. Comment lire ce document

- Ce fichier est la **référence longue**. Il n'est PAS chargé à chaque session IA (coûterait trop de tokens). Il vit dans `docs/`.
- Ce qui est chargé à chaque session = le `CLAUDE.md` racine (court, voir §10).
- La connaissance métier détaillée = **Skills** chargés à la demande (voir §9).
- Convention : `MUST` = obligatoire, `SHOULD` = recommandé, `MAY` = optionnel.

---

## 1. Contexte & vision

### 1.1 Problème
Concevoir des engins mécaniques/robotiques en CAO demande une expertise logicielle (FreeCAD, Python, workbenches métier). L'utilisateur cible **ne veut pas coder** et veut tout piloter en langage naturel, du composant unitaire au projet complet.

### 1.2 Vision
Une IA agit comme **ingénieur CAO + roboticien**. L'utilisateur décrit une intention (« crée un quadricoptère 450mm avec bras repliables »), l'IA modélise, vérifie, assemble, prépare la fabrication, et peut générer la description robot (ROS2). Le tout via MCP, en consommant le minimum de tokens.

### 1.3 Principes directeurs
1. **Zéro-code pour l'utilisateur** après installation.
2. **Token-minimal** = critère d'architecture central, pas une option.
3. **Robustesse** : un crash du noyau géométrique ne doit jamais perdre le travail.
4. **Outils préfaits** : l'IA compose des outils existants plutôt que de réécrire du Python à chaque fois.
5. **Partageable** : repo public, reproductible, documenté, sous licence claire.

---

## 2. Profil utilisateur & contraintes

| Aspect | Valeur |
|---|---|
| Compétence code | Aucune souhaitée ; pilotage 100% langage naturel |
| But | Projet sérieux → partage public GitHub |
| Périmètre voulu | « Tout faire » : méca + robotique + fusée/drone/train + fabrication |
| Contrainte forte | **Minimiser la facture API (tokens)** |
| Livrable attendu | Outils préfaits + canal de communication IA↔FreeCAD + push git public |
| OS | À confirmer (Win/Linux/macOS) — impacte chemins & install |
| FreeCAD | 1.1.1 (stable actuelle) |

### 2.1 Tension assumée : zéro-code vs token-minimal
Piloter en langage naturel consomme des tokens. La §8 décrit les mécanismes concrets pour réduire ce coût (feedback texte-seul, outils préfaits, mémoire projet, skills à la demande).

### 2.2 Limite honnête : la Phase 0 d'installation
L'utilisateur ne code pas, mais **poser** le système (base MCP + workbenches) exige une fois un agent exécutant des commandes. Géré en **Phase 0 assistée** (§11). Après, 100% langage naturel.

---

## 3. Réponse de faisabilité (synthèse technique)

### 3.1 Possible ? OUI pour conception/robotique/fabrication
- **Robotique** : RobotCAD (`drfenixion/freecad.robotcad`) et CROSS (`galou/freecad.cross`) → génèrent URDF/xacro ROS2, cinématique directe/inverse, export Gazebo/RViz. RobotCAD recommande FreeCAD 1.1.x.
- **Fusée** : Rocket Workbench (officiel addons, v5.1.x, MàJ 2026-01).
- **Drone/avion** : workbench Airplane Design (v0.4.x) ; cas publiés d'optimisation VTOL sur FreeCAD.
- **Train / méca générale** : PartDesign + Assembly natifs.
- **Fabrication** : Path/CAM (G-code CNC, post-processeurs multi-contrôleurs) + export STL impression 3D natif.

### 3.2 Limite faible : simulation physique lourde
- **CFD** (aéro fusée/drone) = workbench **CfdOF** (front-end OpenFOAM). Fonctionne pour prototypage, mais coûteux en temps/maillage. Le mainteneur du module CFD recommande lui-même un logiciel commercial pour l'ingénierie sérieuse.
- **FEM** (contraintes) = CalculiX via workbench FEM : correct pour usage courant.
- **Géométrie** : lofting avancé avec courbes guides (fuselages profilés complexes) moins puissant que SolidWorks.

### 3.3 Conclusion
Conception + robotique + fabrication = **bien outillé**. Simulation physique poussée = **maillon faible**, à externaliser (§12). Stratégie « base MCP robuste + greffes workbenches métier + skills + couches maison » = **valide**.

---

## 4. Périmètre fonctionnel détaillé

### 4.1 Modélisation paramétrique (cœur)
- Création primitives (box, cylinder, sphere, cone, torus…).
- Sketch 2D + contraintes ; Pad/Pocket/Revolution/Loft/Sweep.
- Booléens (union/cut/common), patterns (linéaire/polaire/miroir), fillet/chamfer.
- Trous paramétriques (counterbore, countersink), filetages.
- Assemblages multi-pièces, contraintes d'assemblage.
- Paramétrage piloté par feuille de calcul (spreadsheet) → variantes.

### 4.2 Robotique / ROS2
- Définition structure robot : links, joints, collisions, visuals.
- Cinématique directe & inverse.
- Génération package URDF/xacro pour ROS2.
- Export Gazebo (simulation) + RViz (visu).
- Contrôleurs `ros2_control`, capteurs Gazebo.
- Cas multicoptère : PX4 + Gazebo + ROS2 (via RobotCAD).

### 4.3 Aéro — fusée / drone
- Fusée : nez, corps, ailerons, transition ; centre de pression, stabilité.
- Drone : frame, bras, montage moteurs/hélices, logements électronique.
- CFD prototype via CfdOF (RANS, incompressible, steady) — voir limites §3.2/§12.

### 4.4 Train / mécanique
- Châssis, bogies, liaisons, engrenages, transmissions.
- Tolérances, ajustements, jeux fonctionnels.

### 4.5 Fabrication
- CAM/Path : jobs, opérations (profil, poche, perçage, surfaçage), post-processeurs, simulation toolpath.
- Export STL pour impression 3D (vérif watertight, échelle).
- Export STEP/IGES pour échange.

### 4.6 Fonctions transverses
- Captures d'écran isométriques (sur demande, pas par défaut — coût tokens).
- Mesures (distances, volumes, bounding box, masse).
- Import/réparation géométrie externe (STL fournisseur, STEP).

---

## 5. Exigences non-fonctionnelles

| ID | Exigence | Cible |
|---|---|---|
| NF1 | Coût tokens / opération simple | Minimal ; feedback texte-seul par défaut |
| NF2 | Robustesse crash OCCT | Capture + reprise sans perte |
| NF3 | Reproductibilité install | Script/agent unique, multi-OS |
| NF4 | Latence pilotage | Réponse IA → action FreeCAD < quelques s |
| NF5 | Sécurité exécution Python | Sandbox/confiance ; jamais exposer 0.0.0.0 sans IP allowlist |
| NF6 | Portabilité | Win/Linux/macOS (fallback headless) |
| NF7 | Traçabilité | Log opérations + crash reports |
| NF8 | Licence | Open-source compatible (voir §13) |

---

## 6. Architecture technique cible

### 6.1 Vue d'ensemble
```
Utilisateur (langage naturel)
      │
   Client IA  (Claude Code / Desktop / tout client MCP)
      │  protocole MCP (JSON-RPC)
   Serveur MCP FreeCAD  (BASE robuste + greffes)
      │  RPC / socket / XML-RPC (localhost par défaut)
   FreeCAD 1.1.1 + workbenches métier
      │
   Couches maison : Mémoire projet · Vérif géométrique · Checkpoints
```

### 6.2 Choix de la base MCP : **robuste type `blwfish/freecad-mcp`**
**Justification** : quand une IA pilote en autonomie, le risque n°1 n'est pas le manque d'un outil mais un **crash OCCT silencieux** qui fait perdre une session. Base retenue pour : capture crash + Report View, ciblage explicite FreeCAD 1.1.x, install guidée par agent, ~32 outils bien découpés (Part, PartDesign, Sketch, Draft, Boolean, Transform, Measurement). CAM nécessite 1.2-dev (limite connue).

### 6.3 Greffes (capacités importées d'autres serveurs)
| Capacité | Source de référence | Intégration |
|---|---|---|
| Robustesse / capture crash | base (blwfish) | native |
| Largeur outils paramétriques (FEM/BIM/meshing) | `sergiudanstan` (165 outils) | porter via wrappers Python |
| Boucle agentique + simu fluide/FEM | `sandraschi` (FastMCP, 46 outils) | greffe sélective |
| Génération image→3D / texte→3D | `proximile` (TRELLIS/ComfyUI) | optionnel, GPU requis |
| PartDesign paramétrique propre | `contextform` | patterns d'outils |
| Patterns communautaires / option texte-seul | `neka-nat` | inspiration token-saving |

**Clé universelle** : tous ces serveurs exposent l'**exécution Python arbitraire** dans le contexte FreeCAD. Porter un « outil » d'un repo = copier la macro Python et l'envelopper dans le format d'outil de la base. → fusion abordable, pas une réécriture CAD.

### 6.4 Couches maison à créer (voir §7)
Mémoire projet · Vérification géométrique · Checkpoints/rollback.

---

## 7. Composants à créer (le « manquant »)

Aucun serveur existant ne fournit ces trois couches ; elles sont décisives pour un pilotage long et token-efficace.

### 7.1 Mémoire de projet (state persistant)
- **But** : éviter de re-décrire le contexte à chaque session (économie tokens majeure).
- **Contenu** : features créées, intentions de design, contraintes, paramètres, décisions.
- **Forme** : fichier(s) structurés versionnés dans le repo (`/project_state/`), résumables.
- **Règle** : l'IA lit le state au démarrage, le met à jour après chaque étape validée.

### 7.2 Vérification géométrique
- **But** : l'IA valide que sa production est saine avant de continuer.
- **Checks** : self-intersection, volume cohérent, contraintes de sketch satisfaites, watertight (avant STL), masse plausible.
- **Sortie** : verdict texte court (token-efficace) ; capture image seulement si échec ambigu.

### 7.3 Checkpoints / rollback
- **But** : revenir à un état propre quand l'IA s'égare.
- **Forme** : sauvegardes nommées du `.FCStd` + entrée dans le state. Commande naturelle « reviens au checkpoint avant les ailerons ».

---

## 8. Stratégie d'optimisation des tokens (section critique)

Objectif NF1. Mécanismes cumulatifs :

1. **Feedback texte-seul par défaut.** Pas de capture d'écran automatique (les images coûtent cher). Option image uniquement sur demande explicite ou échec de vérif ambigu. (Mécanisme déjà présent dans certains serveurs type neka-nat.)
2. **Outils préfaits > Python ad hoc.** Chaque opération récurrente = un outil nommé. L'IA appelle l'outil (quelques tokens) au lieu de générer un script (beaucoup de tokens) à chaque fois.
3. **Mémoire projet (§7.1).** Le contexte n'est pas re-décrit ; l'IA lit un state compact.
4. **Skills à la demande (§9).** La connaissance métier lourde n'est chargée que quand le domaine est actif.
5. **`CLAUDE.md` court (§10).** Chargé chaque session → doit rester < ~150 lignes.
6. **Réponses verbeuses interdites côté serveur.** Les outils renvoient des résultats compacts (IDs, statut), pas des dumps complets de scène sauf demande.
7. **Batch d'opérations.** Regrouper les étapes liées en un appel quand possible.
8. **Modèle adapté.** Tâches simples sur modèle léger ; planification sur modèle lourd (choix côté client IA).
9. **`/clear` fréquent.** Vider le contexte entre tâches indépendantes pour éviter le « context rot ».

---

## 9. Couche connaissance métier (Skills)

Principe : **un Skill par domaine**, chargé seulement quand pertinent (token-minimal). Structure `skills/<nom>/SKILL.md` + ressources optionnelles.

| Skill | Déclencheur | Contenu |
|---|---|---|
| `skill-partdesign` | modélisation pièce | recettes Pad/Pocket/Loft, contraintes, patterns |
| `skill-assembly` | multi-pièces | contraintes d'assemblage, structure |
| `skill-robotics-ros` | robot/ROS/URDF | RobotCAD/CROSS : links/joints, génération URDF, Gazebo |
| `skill-rocket` | fusée | Rocket WB : nez/ailerons, stabilité, CP |
| `skill-drone` | drone/quad | frame, bras, moteurs, électronique |
| `skill-cfd` | aéro/simulation fluide | CfdOF : domaine, maillage, RANS, limites |
| `skill-fem` | contraintes/résistance | CalculiX, charges, matériaux |
| `skill-cam` | usinage/CNC | Path : jobs, opérations, post-proc, simu |
| `skill-print3d` | impression | STL watertight, échelle, orientation |
| `skill-verify` | vérification | checks géométriques (§7.2) |

Chaque SKILL.md : court, concret, avec invocations exactes ; détails lourds en fichiers annexes chargés au besoin.

---

## 10. Fichier `CLAUDE.md` racine (à créer, court)

Chargé **chaque** session → budget strict < ~150 lignes. Contenu cible :
- 1 ligne : ce qu'est le repo.
- Stack & versions (FreeCAD 1.1.1, base MCP, OS).
- Commandes clés (démarrer serveur MCP, lancer FreeCAD headless, checkpoint, push git).
- Architecture en 5 lignes (pointeurs vers dossiers, pas de prose).
- Règles d'or token-minimal (texte-seul par défaut, lire le state au start, MàJ state après étape).
- Anti-patterns réels (« ne pas faire de screenshot auto », « ne pas re-décrire la scène »).
- Imports `@docs/CAHIER_DES_CHARGES.md` et `@project_state/…` à la demande.
> Règle : si retirer une ligne ne provoque pas d'erreur de l'IA → la supprimer.

---

## 11. Déploiement par phases

| Phase | Contenu | État final |
|---|---|---|
| **0 — Install assistée** | Agent installe base MCP + workbenches (RobotCAD, Rocket, CfdOF, Path…), pose `CLAUDE.md` + skills | Système opérationnel |
| **1 — Base seule** | Faire tourner la base, vérifier pilotage FreeCAD 1.1.1, créer une pièce simple | Pilotage nominal validé |
| **2 — Greffe #1** | Ajouter 1 bloc (ex. robotique ROS), tester, valider | 1 domaine métier actif |
| **3 — Greffes itératives** | Ajouter domaines un par un (fusée, drone, CFD, CAM), tester à chaque fois | Couverture large |
| **4 — Couches maison** | Mémoire projet, vérif, checkpoints | Pilotage long robuste |
| **5 — Publication** | Doc, licence, install reproductible, push public | Repo partageable |

**Règle d'or** : jamais tout intégrer d'un coup. Une greffe → un test → validation → greffe suivante.

---

## 12. Limites assumées & solutions de repli

| Limite | Repli |
|---|---|
| CFD lourd (aéro précise) | Prototype CfdOF en interne ; export STL/STEP vers OpenFOAM standalone ou outil commercial pour étude sérieuse |
| Lofting avancé (fuselages complexes) | Combiner Curves/Surface WB ; ou modélisation hybride, import STEP partiel |
| CAM avancé (multi-axes) | CAM de base sur 1.1.x ; suivre 1.2-dev pour Path complet |
| Génération 3D par IA (image→3D) | Optionnel, nécessite GPU (proximile/TRELLIS) ; hors MVP |
| Simulation dynamique multicorps | Externaliser vers Gazebo (déjà dans pipeline ROS) |

---

## 13. Préparation au partage public GitHub

- **Licence** : choisir compatible avec dépendances (FreeCAD = LGPL ; workbenches variés LGPL/GPL). Vérifier compat avant choix final (probable LGPL-2.1+ ou GPL selon greffes).
- **Structure repo** :
```
/CLAUDE.md                 # court, chargé chaque session
/docs/CAHIER_DES_CHARGES.md
/skills/<domaine>/SKILL.md
/server/                   # base MCP + greffes
/project_state/            # mémoire projet (peut être .gitignore selon confidentialité)
/checkpoints/              # sauvegardes nommées
/install/                  # script/agent Phase 0
/README.md                 # quickstart + démo
/LICENSE
```
- **README** : pitch 1 ligne, prérequis, install agent, 1 exemple « du langage naturel à la pièce ».
- **Reproductibilité** : install pilotée par agent (pointer l'IA vers `install/AGENT-INSTALL.md`).
- **Sécurité** : ne jamais committer de secrets ; `deny` lecture `.env` côté client ; RPC sur localhost, allowlist IP si distant.
- **CI (SHOULD)** : tests dispatch outils + intégration contre FreeCAD live.

---

## 14. Critères de réussite & livrables

### 14.1 Critères
- [ ] Créer une pièce paramétrique simple en langage naturel, sans toucher au code.
- [ ] Générer un robot basique → package URDF ROS2 exporté.
- [ ] Modéliser un drone/fusée prototype + vérif géométrique passée.
- [ ] Générer un G-code ou STL exploitable.
- [ ] Reprendre un projet entre 2 sessions via la mémoire projet (sans re-décrire).
- [ ] Survivre à un crash OCCT et reprendre via checkpoint.
- [ ] Coût tokens d'une opération simple maintenu minimal (texte-seul).
- [ ] Repo public installable par un tiers en suivant le README.

### 14.2 Livrables
1. `CLAUDE.md` racine court.
2. Ce cahier des charges (`docs/`).
3. Serveur MCP (base + greffes) installé.
4. Skills par domaine.
5. Couches maison (mémoire, vérif, checkpoints).
6. Script/agent d'installation.
7. README + LICENSE.

---

## 15. Décisions ouvertes — TRANCHÉES (mise à jour 2026-07-03)

| # | Décision | Choix retenu |
|---|---|---|
| D1 | OS principal | **Windows** (validé bout-en-bout ; architecture multi-OS, Linux/macOS non testés) |
| D2 | Licence finale | **CC BY-NC 4.0** — libre de télécharger/modifier/partager, citation obligatoire (auteur + projets tiers de CREDITS.md), pas d'usage commercial. Possible car ni la base ni les workbenches (LGPL) ne sont vendorisés : ils sont clonés à l'install. **Remplacé en v2 (2026-10-12)** : PolyForm Noncommercial 1.0.0 (utiliser/modifier/partager librement, jamais vendre) ; la base `server/freecad-mcp/`, désormais intégrée et modifiée, reste LGPL-2.1-or-later |
| D3 | Génération image→3D incluse ? | **Non** (hors MVP, GPU requis) |
| D4 | `project_state` public ou privé | **Privé** (gitignoré ; contient les projets de l'utilisateur) |
| D5 | Niveau CAM visé | **Différé à FreeCAD 1.2** ; en attendant, repli STEP/STL (`skill-cam` documente la conduite à tenir) |

### 15.1 Divergences assumées vs cette spécification
Voir `docs/ROADMAP.md` (audit détaillé). Résumé :
- `skill-cfd` livré comme **workflow de repli** (§12 : analytique interne + export vers OpenFOAM
  externe), pas comme CFD interne ; `skill-cam` livré comme **stub** documentant la limite 1.2-dev.
- Deux skills non prévus ajoutés : `skill-exchange` (STEP/IGES) et `skill-gear` (engrenages).
- Robotique : greffe **CROSS** retenue (URDF/xacro sans runtime ROS2) au lieu de RobotCAD.
- R10 (spawn Windows) corrigé par patch maison : FreeCAD est auto-démarré par le bridge.

---

## Annexe A — Comparatif serveurs MCP FreeCAD

| Repo | Outils | Force | Usage ici |
|---|---|---|---|
| `blwfish/freecad-mcp` | ~32 | Robustesse, crash OCCT, ciblage 1.1.x, install agent | **BASE** |
| `sergiudanstan/freecad-mcp` | 165 | Largeur (FEM/BIM/sketch/mesh), Node/TS | Source de greffes outils |
| `sandraschi/freecad-mcp` | 46 | FastMCP, boucle agentique, CFD/FEM/print, dashboard | Greffes simu/agentique |
| `proximile/FreeCAD-MCP` | 57 | Docker headless, image→3D, multi-IA | Génération 3D (optionnel) |
| `contextform/freecad-mcp` | — | PartDesign propre, installeur npm | Patterns PartDesign |
| `neka-nat/freecad-mcp` | — | Populaire, option texte-seul token-saving | Inspiration & patterns |
| `spkane/...robust-mcp` | 150+ | Multi-mode, conteneurisable | Référence robustesse |

## Annexe B — Workbenches métier retenus

| Workbench | Domaine | Note |
|---|---|---|
| PartDesign / Sketcher / Assembly | méca cœur | natif |
| RobotCAD (`drfenixion`) | robotique ROS2 | recommande 1.1.x |
| CROSS (`galou`) | robotique ROS | URDF/xacro, IK |
| Rocket WB | fusée | v5.1.x, MàJ 2026-01 |
| Airplane Design | drone/avion | v0.4.x |
| CfdOF | CFD | front-end OpenFOAM, limites §3.2 |
| FEM | contraintes | CalculiX |
| Path / CAM | usinage | post-proc multi-contrôleurs, CAM complet en 1.2-dev |

## Annexe C — Risques

| Risque | Gravité | Mitigation |
|---|---|---|
| Crash OCCT silencieux | élevé | base avec capture (§6.2) + checkpoints |
| Explosion coût tokens | élevé | §8 intégral |
| Incompat licences à la publication | moyen | vérif §13 avant push |
| Fusion « monstre » ingérable | moyen | greffes une par une (§11) |
| CFD insuffisant pour usage sérieux | moyen | externalisation (§12) |
| Dépendance 1.2-dev pour CAM | faible | rester 1.1.x sauf besoin avéré |
