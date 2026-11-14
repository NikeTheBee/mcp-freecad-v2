# Atelier d'ingénierie local : feuille de route

Concevoir, simuler, tester virtuellement, puis fabriquer **n'importe quel** projet de robotique,
d'électronique ou de mécatronique, dans l'air, l'espace, sur terre, sous terre, sur l'eau et sous l'eau.

> Rédigé le 2026-11-14, à partir de dix recherches sur les outils libres. Versions et licences
> relevées ce jour-là sur les sites officiels, GitHub, PyPI et conda-forge.
> **Rien de ce qui suit n'est encore installé ni testé ici.** Un outil n'entre dans le projet qu'après
> avoir refait son cas de référence (§9). L'installeur figera les versions exactes et leurs empreintes.
>
> **Statut : proposition.** Elle attend les choix du propriétaire (§13).

---

## 1. Ce qui est demandé

- **Aucune limite de domaine.** Fusée, drone et humanoïde ne sont que des exemples. Tout projet de
  robotique, d'électronique ou de mécatronique doit être possible :
  - dans l'air et l'espace ;
  - sur terre (véhicules, robots à pattes, machines) ;
  - sous terre (forage, tunnel, inspection de conduites) ;
  - sur et sous l'eau (propulseur, drone sous-marin, bouée, ROV).
- **L'électrique fait partie du projet.** Circuits, puissance, moteurs, batteries, câblage, radio,
  microprogramme.
- **La finalité est l'objet réel.** Chaque projet doit pouvoir être fabriqué, monté, essayé, et
  comparé à sa simulation.
- **Une boucle complète :** besoin → hypothèses → modèle → simulation → test virtuel → résultats →
  correction. On recommence jusqu'à ce que tout passe, puis fabrication → essai réel → recalage du
  modèle.
- **Les contraintes :**
  - gratuit et libre, local, sécurisé ;
  - économe en tokens ;
  - environnements propres et compatibles entre eux ;
  - outils natifs Windows en priorité ;
  - une seule commande d'installation, plug and play ;
  - des skills poussés.

---

## 2. Principes : le pourquoi

| # | Principe | Pourquoi |
|---|---|---|
| P1 | **Un seul contrat pour tous les outils.** Chaque logiciel est enveloppé par un *adaptateur* : il valide les entrées, lance l'outil à part, puis renvoie un verdict `OK / FAIL / UNKNOWN` et quelques chiffres. Le journal complet reste sur le disque. | L'IA lit une mesure et un verdict, elle ne croit pas un outil sur parole. C'est aussi ce qui économise les tokens. |
| P2 | **Aucun solveur sans cas de référence** (NAFEMS, formule exacte, données publiées). Un écart rend l'outil `UNKNOWN`. | Un solveur mal réglé donne une belle image fausse. |
| P3 | **Les environnements Python ne se mélangent pas.** FreeCAD 1.1 tourne sous Python 3.11 avec numpy 1.26 et VTK 9.3. La plupart des outils récents veulent numpy 2, Python 3.12 ou plus, ou leur propre VTK. Chaque famille d'outils a son environnement ; les échanges passent par des **formats ouverts** (§8). | Installer ces outils dans le Python de FreeCAD le casserait. Vérifié : l'atelier Rocket 5.1.3 exige déjà numpy ≥ 2.4.1, et PyChrono n'existe pas pour Python 3.11. |
| P4 | **Natif Windows d'abord, WSL2 en voie optionnelle.** | WSL2 demande les droits administrateur et double les environnements. Il ne sert qu'à ce qui n'existe pas nativement (§11). |
| P5 | **Sécurité par défaut** (§10). Aucun port réseau sauf la boucle locale, aucun serveur MCP tiers qui exécute du code. Jamais de G-code écrit par l'IA, jamais de commande vers une vraie machine ou un vrai véhicule. | Un modèle de langage, ou un fichier piégé, ne doit pas pouvoir agir sur le monde physique. |
| P6 | **Une greffe à la fois** (cahier des charges §11). | Quand tout arrive d'un coup, on ne sait plus ce qui casse. |
| P7 | **L'IA pilote, elle ne regarde pas défiler les itérations.** Balayages et optimisations tournent seuls, en local ; l'IA ne reçoit que le résumé. | 200 itérations ne coûtent aucun token si l'IA n'est pas dans la boucle. |
| P8 | **Le réel ferme la boucle.** Les mesures faites sur le prototype sont comparées à la simulation, et les paramètres du modèle sont recalés. | Une simulation non confrontée au réel reste une hypothèse. |
| P9 | **Les limites de sécurité électrique et physique sont des exigences** (§10.2), vérifiées comme une cote. | Une batterie, une tension ou un caisson sous pression mal dimensionnés blessent. |
| P10 | **Petit noyau, packs de domaine.** Un socle commun (structure, thermique, fluides, dynamique, électrique, fabrication) sert tous les domaines. Chaque domaine ajoute un *pack* : adaptateurs, modules maison, skill et cas de référence. | On ajoute un milieu (l'eau, le sous-sol...) sans toucher au reste. |

---

## 3. Architecture : le comment

```
Utilisateur (langage naturel)
   └─ Client IA compatible MCP (Claude Code ; aussi Claude Desktop, VS Code, LM Studio...)
        ├─ MCP freecad          (existant) modélisation dans FreeCAD
        └─ MCP freecad-layers   (existant) contrat, check, export, fem_static...
              + simulate / job / study / tools_status   (NOUVEAUX)
                    │  sous-processus, sans shell, délai maximal, limite mémoire, pas de réseau
                    ▼
              server/adapters/<outil>/run.py   (dans l'environnement pixi de l'outil)
                    │  entrée JSON → outil → sortie JSON (verdict + chiffres + chemins)
                    ▼
              solveurs, simulateurs, chaînes électroniques et de fabrication (§4 à §7)
```

### 3.1 Pourquoi pas un serveur MCP par outil
- **Les tokens :** chaque serveur MCP ajoute ses descriptions d'outils à *chaque* session. Quatre
  outils génériques coûtent bien moins que soixante outils spécialisés.
- **La sécurité :** les dizaines de serveurs MCP tiers relevés présentent tous au moins un de ces
  défauts, et aucun n'est assez sûr pour être adopté tel quel (§14) :
  - ils exécutent du code arbitraire ;
  - ils écoutent sans authentification (rosbridge, ParaView, Blender) ;
  - ils peuvent armer un vrai drone ou lancer une impression ;
  - ils envoient les données sur Internet.

  On reprend leurs bonnes idées, pas leur code. Les meilleures : `ogive` (OpenRocket, dossier racine
  imposé, reçus sha256) et `mcp-calculix` (pas de fichier d'entrée brut, boucle locale seulement).

### 3.2 Les quatre nouveaux outils MCP

| Outil | Rôle |
|---|---|
| `simulate(kind, target, params)` | Lance une simulation d'un type donné (`fem_modal`, `thermal`, `cfd_compressible`, `hydrostatics`, `rov_sitl`, `rover_terrain`, `spice`, `orbit`...). Renvoie le verdict et les chiffres, ou un numéro de tâche si le calcul est long. |
| `job(id, action)` | Suit une tâche longue : état, résultat, annulation. Une ligne par appel. |
| `study(...)` | La boucle d'étude (§3.4) : hypothèses, paramètres, critères, itérations, optimisation, journal, recalage sur mesures. |
| `tools_status()` | Ce qui est installé, avec les versions, le résultat du cas de référence, et ce qu'il manque avec la commande pour l'installer. |

Le détail de chaque `kind` est dans le skill de son pack, chargé seulement quand on en a besoin.

### 3.3 Contrat d'un adaptateur
```json
// entrée (écrite par freecad-layers dans le dossier de l'étude)
{"kind": "fem_buckling", "geometry": "etude/coque.step", "params": {"pressure_MPa": 1.2, "material": "aluminium"},
 "limits": {"min_safety_factor": 3}, "timeout_s": 900}
// sortie (résumée en 3 à 10 lignes pour l'IA)
{"verdict": "OK", "summary": "flambage à 4.9 MPa (linéaire) ; facteur de réduction 0.5 → coef. de sécurité 2.04 … FAIL",
 "values": {"p_cr_MPa": 4.9}, "files": ["etude/fem_buckling/result.vtu"],
 "tool": "calculix 2.23", "reference_case": "Euler OK", "inputs_hash": "sha256:…"}
```
- **Le cache :** un même `inputs_hash` (géométrie + paramètres + version de l'outil) renvoie le résultat
  déjà calculé. Chaque résultat est reproductible.
- **Pas de shell :** les exécutables sont appelés avec une liste d'arguments. Les scripts envoyés sur
  l'entrée standard (XFOIL, AVL, BELLHOP) sont générés par l'adaptateur.

### 3.4 La boucle d'étude (`study`)
```
exigences (require) ─┐
hypothèses ──────────┤   charges, matériau, courant marin, sol, températures, tension... chacune écrite et datée
paramètres ──────────┤   cellules du tableur FreeCAD : une seule source pour la CAO, la simulation,
                     │   l'URDF et les constantes du microprogramme
                     ▼
   modèle → check (géométrie + contrat) → simulate × N → critères → verdict de l'itération
                     │                                        │
                     │  FAIL : correction                     │  tout OK : fabrication (§7)
                     │   - l'IA propose, ou                   ▼
                     │   - balayage / optimisation locale   essai réel (benchtest) → mesures
                     │     (Optuna, pymoo, BoTorch)            → recalage (calib) → nouvelle boucle
                     └── checkpoint avant chaque essai, restore si c'est pire
```
- **Le journal :** une ligne par itération. Il produit un **dossier de justification** : exigences,
  hypothèses, résultats, versions des outils, cas de référence, essais réels, écarts.
- **La traçabilité :** chaque exigence est liée à sa vérification et à son essai (Doorstop ↔ `require`
  ↔ résultats).

### 3.5 Arborescence
```
MCP FREECAD/
├─ pixi.toml · pixi.lock          environnements verrouillés (sha256 de chaque paquet)
├─ install/
│   ├─ install.ps1                LA commande unique (§8)
│   ├─ bootstrap.py               existant, étendu par profils
│   └─ tools.lock.json            outils hors conda : URL officielle + SHA256 + licence
├─ server/
│   ├─ freecad-mcp/               base (existant)
│   ├─ freecad_layers/            couches (existant) + sim.py (orchestrateur) + study.py
│   ├─ adapters/                  un dossier par outil externe (run.py, README, cas de référence)
│   ├─ modules/                   modules maison validés, rangés par pack (hydrostatics, terramech, wire_sizing...)
│   └─ layers_mcp/                outils MCP (existant) + simulate / job / study / tools_status
├─ packs/                         un manifeste par pack : outils, environnements, skills, cas de référence
├─ skills/                        un skill par pack, plus les transverses
├─ validation/                    cas de référence par physique (§9), lancés par run_all_tests
├─ tools/        (git-ignoré)     binaires téléchargés et vérifiés
├─ .pixi/        (git-ignoré)     environnements
└─ docs/
```
La racine d'installation se règle (`MCPFC_HOME`). Les environnements et le cache pixi la suivent :
rien de lourd ne va sur le disque système s'il est plein.

### 3.6 Environnements pixi (compatibles entre eux par les fichiers, pas par les imports)
| Environnement | Python | Contenu |
|---|---|---|
| `mcp` | 3.11 | le pont MCP actuel, qui remplace `~/.freecad-mcp/venv` |
| `sim` | 3.12 | structure, thermique, fluides, optimisation, post-traitement, fusée, aéro, marin |
| `robot` | 3.12 | MuJoCo, Pinocchio, PyChrono, commande, vision |
| `elec` | 3.12 | spicelib, PyBaMM, Magpylib, scikit-rf, WireViz, PlatformIO |
| `geo` | 3.12 | OpenGeoSys, Kratos, gprMax |
| `space` | 3.12 | Basilisk, Orekit (Java), GMAT |
| `rf` | 3.13 | openEMS (ses roues Python n'existent qu'en 3.13+) |
| `ros` (option) | RoboStack | ROS 2 Lyrical, Nav2, MoveIt, Gazebo Jetty |
| `post` (option) | 3.12 | ParaView `pvbatch` |

Des environnements plus petits veulent dire moins de conflits. Un environnement ne s'installe que si
un pack qui en a besoin est choisi.

### 3.7 Ajouter n'importe quel outil : la procédure
« Zéro limite », c'est pouvoir ajouter proprement un outil qui n'est pas encore là. Chaque ajout suit
la même fiche :
1. **Licence :** libre et vérifiée. Si elle n'est pas libre, l'outil est refusé ou réservé à un usage
   à la main.
2. **Fonctionnement :** natif Windows, sinon WSL2 ; et pilotable sans interface (ligne de commande ou
   API).
3. **Version figée :** version, empreinte SHA256 et source officielle, dans `pixi.lock` ou
   `tools.lock.json`.
4. **Adaptateur :** entrées validées, sortie JSON, délai maximal, pas de réseau, chemins par
   `safe_out_path`.
5. **Cas de référence** dans `validation/`, en échec si on casse l'adaptateur.
6. **Skill :** quand l'utiliser, ses limites, comment lire ses résultats.
7. **Entrée dans un pack,** puis suite complète au vert.

Un outil qui ne passe pas les étapes 1 à 5 n'est pas branché.

---

## 4. Le socle commun (sert tous les domaines)

| Physique | Outil principal (natif Windows) | Recoupement | État aujourd'hui |
|---|---|---|---|
| Statique : force, **pression, gravité, rotation** | CalculiX (fourni avec FreeCAD) | MYSTRAN, formules | forces seules → à étendre |
| Flambage (coques sous pression comprises) | CalculiX `buckling`, avec facteur de réduction, ou non linéaire avec défaut initial | Euler, NASA SP-8007 | à ajouter |
| Vibrations, modes propres | CalculiX `frequency` | NAFEMS FV52 | à ajouter |
| Thermique (conduction, convection, rayonnement, transitoire) | CalculiX `thermomech` | Elmer, NAFEMS T4 | à ajouter |
| **Dilatation**, contraintes thermiques | CalculiX thermomécanique couplé | NAFEMS LE11, bilame | à ajouter |
| Plasticité, grandes déformations, **contact** | CalculiX non linéaire | plaque trouée, Hertz | à ajouter |
| **Compression** de mousse, joints, élastomères | CalculiX par injection dans le fichier `.inp` | essai analytique | plus tard |
| Fatigue | pyLife (courbes S-N, Miner, rainflow) ; FLife (vibrations aléatoires) | ASTM E1049 | à ajouter |
| Fluide lent, thermique fluide + solide | Elmer *Flow + Heat* ; OpenFOAM via CfdOF | Poiseuille, Schäfer-Turek | absent |
| Fluide compressible, supersonique | SU2 | cas NASA TMR | absent |
| Surface libre, cavitation, hélices | OpenFOAM (interFoam, MRF, interPhaseChangeFoam) | essais publiés | absent |
| Acoustique, vibro-acoustique | Elmer (Helmholtz), NGSolve ; openCFS | formules | absent |
| EDP sur mesure (n'importe quelle physique) | NGSolve (formes faibles en Python, natif) | solutions exactes | absent |
| Dynamique multicorps | MuJoCo 4 ; PyChrono 10 | pendule, chute libre | absent |
| Commande : PID, LQR, MPC, identification | python-control, CasADi, SysIdentPy | — | absent |
| Optimisation, sensibilité, substituts | Optuna, pymoo, SALib, BoTorch, SMT, scikit-learn | — | absent |
| Post-traitement numérique | PyVista / VTK (dans leur environnement) ; ParaView `pvbatch` | — | — |
| **Animation** | FreeCAD : GIF d'un mécanisme sur sa course ; MuJoCo et Chrono : vidéo hors écran ; trajectoires | — | absente |

---

## 5. Les packs de domaine

Chaque pack précise ses outils (natifs, sauf mention WSL2), ses modules maison (petits, validés), ses
tests virtuels et ce qu'il ne fait pas.

### 5.1 Air : drones, avions, VTOL
- **Outils :**
  - aéro et dimensionnement : AeroSandbox + NeuralFoil, XFOIL, AVL ;
  - hélice et moteur : QPROP ;
  - dynamique de vol : JSBSim ;
  - vol avec le vrai pilote automatique : **ArduPilot SITL** (binaires Windows) + pymavlink ;
  - commande : python-control ;
  - multirotor : RotorPy.
- **Tests virtuels :** vol stationnaire, réponse à un échelon, rafale, mission complète.
- **WSL2 :** PX4 SIH, Gazebo + PX4.
- **Données :** hélices UIUC et T200 (Blue Robotics), citées, jamais redistribuées.

### 5.2 Fusées et propulsion (véhicule + moteur de niveau étudiant)
- **Outils :**
  - stabilité : atelier Rocket (existant, Barrowman) ;
  - vol 6 DDL et Monte-Carlo : **RocketPy** ;
  - recoupement : OpenRocket (piloté par orlab) ;
  - courbes de poussée : ThrustCurve, mises en cache avec leur licence ;
  - thermochimie : **NASA CEA** (libre, Apache-2.0) ;
  - propriétés des fluides et des gaz : CoolProp, Cantera ;
  - profil de tuyère : pygasflow (Rao), vérifié avec SU2.
- **Modules maison :** refroidissement régénératif 1D (Bartz + CoolProp), chambre sous pression
  (formules + CalculiX).
- **Tests virtuels :** marge statique en sortie de rampe, accélération, flottement des ailerons,
  ellipse d'atterrissage.
- **Hors périmètre :** composition et fabrication de propergol solide ; tout usage d'arme.

### 5.3 Espace : petits satellites
- **Outils :**
  - orbite et mission : GMAT (en mode lot), Orekit (via orekit_jpype) ;
  - attitude, commande, Monte-Carlo : **Basilisk** ;
  - thermique : Elmer avec rayonnement, ou réseau thermique sous OpenModelica, plus pyViewFactor ;
  - rayonnement : SpacePy/IRBEM (AE8/AP8).
- **Sources de charges :** spécification CubeSat Rev 14.1, NASA GEVS, guides des lanceurs.
- **Écartés :** SPENVIS (web) et AE9/AP9 (code non ouvert).

### 5.4 Terre : véhicules, robots à roues, à chenilles, à pattes, humanoïdes
- **Outils :**
  - véhicules et sol déformable : **PyChrono 10** (Chrono::Vehicle, sol SCM) ;
  - robots à pattes et humanoïdes : **MuJoCo 4** + Menagerie (11 humanoïdes de référence) ;
  - dynamique : Pinocchio (conda-forge) ;
  - cinématique inverse : mink, Pink ;
  - commande : CasADi ;
  - vision et nuages de points : OpenCV, Open3D, PCL ;
  - localisation sans GPS : RTAB-Map ;
  - option ROS 2 Lyrical via RoboStack (Nav2, slam_toolbox, MoveIt, ros2_control).
- **Modules maison :**
  - pré-dimensionnement sur sol (`terramech`, Bekker-Wong/Janosi) ;
  - équilibre ZMP et capture point, validé sur le modèle G1 de Menagerie ;
  - dimensionnement des actionneurs à partir de `mj_inverse` (couple efficace, échauffement) ;
  - export FreeCAD → Chrono ;
  - couverture des capteurs (`sensor_cov`).
- **Tests virtuels :** pente limite, franchissement, tenir debout, poussée, marche.
- **WSL2 :** OMPL en Python, crocoddyl, TSID, Placo, apprentissage JAX sur carte graphique.

### 5.5 Sous terre : forage, tunnel, inspection de conduites, mine
- **Outils :**
  - mécanique des sols, excavation : Kratos GeoMechanics (licence BSD-4, à faire valider) ;
  - couplage thermique, hydraulique et mécanique dans la roche et le sol (forages, sol gelé) :
    **OpenGeoSys** ;
  - radar de sol : **gprMax** ;
  - écoulement dans un tunnel : OpenFOAM ;
  - localisation sans GPS : RTAB-Map, KISS-ICP.
- **Modules maison :** cinématique d'un robot en conduite (`pipe_kin`), réseau de ventilation
  (`vent_net`, Atkinson, Hardy-Cross), préparation des cas gprMax (`gpr_wrap`).
- **WSL2 :** DEM-Engine (grains sur carte graphique, interaction outil-sol), Yade.

### 5.6 Surface et sous-marin : ROV, AUV, propulseurs, bouées, bateaux
- **Outils :**
  - masse d'eau entraînée, amortissement, houle : **Capytaine 3** (Apache-2.0) ;
  - câble ombilical et ancrage : **MoorDyn** ;
  - simulation de véhicule : **Gazebo Jetty** (systèmes flottabilité, hydrodynamique et propulseur,
    natif via conda-forge, version postérieure à avril 2026) ;
  - vrai pilote automatique : **ArduSub SITL** (binaire Windows, empreinte relevée par nous) ;
  - modèles de référence : PythonVehicleSimulator (modèles de Fossen : REMUS100, Otter) ;
  - flambage de coque : CalculiX ;
  - acoustique sous-marine : arlpy + BELLHOP.
- **Modules maison :**
  - `hydrostatics` : flottabilité, centre de carène, stabilité, assiette, lest, mousse ;
  - `fossen_params` : masses ajoutées et amortissements, exportés vers Gazebo et MuJoCo ;
  - `thruster` : courbes de poussée, série B de Wageningen, cavitation, allocation des propulseurs ;
  - `pressure_hull` : coque sous pression, d'après NASA SP-8007 ;
  - `oring_gland` : gorges de joints toriques, tailles AS568 et ISO 3601 ;
  - `galvanic` : couples de corrosion en eau de mer, d'après MIL-STD-889 ;
  - `tether_static` : chaînette du câble avec le courant ;
  - `sonar_budget` : bilan sonar.
- **Tests virtuels :** équilibre de flottabilité, tenue de cap et de profondeur, poussée contre
  courant, coque à la profondeur maximale × coefficient de sécurité.
- **WSL2 :** Stonefish (sonar et caméra réalistes), POSIM (successeur de DAVE).

### 5.7 Électrique et électronique
- **Outils :**
  - circuits et puissance : ngspice + **spicelib**, Xyce en option ;
  - circuit imprimé : **KiCad 10** (`kicad-cli` : ERC, DRC, Gerber, STEP) ;
  - batteries : **PyBaMM**, liionpack en version figée ;
  - aimants et bobines : Magpylib ;
  - moteurs et magnétisme : Elmer, GetDP/ONELAB ;
  - inductances et transformateurs : FEMMT, PyMKF ;
  - radio et compatibilité électromagnétique : openEMS, scikit-rf ;
  - faisceaux de câbles : **WireViz** ;
  - systèmes complets (moteur + réducteur + batterie) : OpenModelica (AGPL) / FMPy.
- **Modules maison :**
  - `wire_sizing` : section, chute de tension, fusibles, avec des tables sourcées ;
  - `power_budget` : rails, courants crête et efficace, taux de décharge, autonomie ;
  - `spice_gate` : circuit dans un bac à sable, mesures contre un contrat ;
  - `kicad_gate` : ERC et DRC propres, isolement selon la tension ;
  - `motor_sizing` : couple, constante de vitesse, échauffement ;
  - `pack_model` : batterie complète.
- **Conception carte et boîtier ensemble :** `kicad-cli pcb export step` → FreeCAD → `check` (la
  carte rentre dans le boîtier, pas de collision). KiCad StepUp viendra seulement après validation
  sur FreeCAD 1.1.
- **Écartés :**
  - FEMM, et donc pyleecan : licence non libre, et pyleecan exige Python < 3.11 ;
  - PySpice : abandonné ;
  - LTspice et QSPICE : propriétaires.

### 5.8 Mécatronique embarquée
- **Outils :**
  - PlatformIO, Arduino CLI, Zephyr SDK, ESP-IDF, Arm GNU + OpenOCD ;
  - commande de moteurs : SimpleFOC, VESC, AM32 ;
  - microprogramme sans carte : **Renode**, QEMU Espressif ;
  - logique programmable (FPGA) : OSS CAD Suite ;
  - mesure au banc : sigrok-cli, PyVISA + pyvisa-py.
- **Ce que fait l'IA :** elle écrit et compile le microprogramme, puis le teste en émulation.
  **Charger un programme sur une vraie carte reste une action de l'utilisateur.**
- **Écartés :** micrologiciel fermé de l'ODrive Pro/S1, STM32CubeCLT (propriétaire).

---

## 6. Physique non couverte nativement sous Windows

Ces besoins passent par WSL2 (scénario B) ou sont hors périmètre :
- couplage fluide-structure partitionné (preCICE) ;
- solveurs OpenFOAM compilés sur mesure ;
- gros calculs MPI ;
- grains sur carte graphique (DEM-Engine) ;
- sonar et caméra sous-marins réalistes (Stonefish) ;
- crocoddyl, TSID, Placo ;
- apprentissage JAX sur carte graphique ;
- MOOSE, DUNE.

---

## 7. Du modèle à l'objet réel

### 7.1 Pièces du commerce et fabricabilité
- **Pièces du commerce :**
  - visserie ISO/DIN : atelier Fasteners (filetage géométrique désactivé par défaut) ;
  - engrenages : freecad.gears ;
  - bibliothèque de pièces FreeCAD : clonée partiellement, licence CC-BY à citer ;
  - roulements et profilés : générateurs maison (`stdparts`).

  La nomenclature distingue les pièces achetées des pièces fabriquées.
- **Fabricabilité (`dfm`, maison) :**
  - rayon intérieur ≥ rayon de l'outil, accès et profondeur d'usinage ;
  - rayon de pliage et distances en tôlerie ;
  - dépouille selon la direction de démoulage ;
  - impression 3D (existant : `printability`).
- **Tolérances (`tolstack`, maison) :** chaînes de cotes au pire cas, en RSS et en Monte-Carlo, liées
  à `require`. Les tolérances restent portées par le plan 2D : le STEP AP242 ne transporte pas encore
  la cotation de façon fiable.

### 7.2 Fabrication

| Procédé | Ce que fait l'IA | Ce que fait l'humain |
|---|---|---|
| Impression 3D | `printability` puis `export` 3MF vérifié. Option : projet PrusaSlicer (`--export-3mf`) avec **le profil de l'utilisateur**, sans G-code. | Découpe en tranches, vérifie l'aperçu, imprime. |
| Usinage | Livre le STEP et le plan TechDraw. **Lit** le G-code de l'humain avec CAMotics (`camsim` → STL de la pièce usinée), puis le compare au modèle avec `verify`. | Programme les trajectoires (FreeCAD CAM, Kiri:Moto), puis les envoie (UGS, Candle). |
| Découpe laser, tôlerie | Déplie la pièce (SheetMetal) en DXF ; imbrication avec sparrow. | Découpe (LaserGRBL). |
| Circuit imprimé | Fichiers Gerber, perçage, implantation et nomenclature (`kicad-cli`) ; aide au montage (InteractiveHtmlBom) ; rapport DRC/ERC. | Commande chez le fabricant, câble. |
| Faisceau de câbles | Plan et nomenclature WireViz, sections vérifiées (`wire_sizing`). | Sertit et monte. |
| Achats | Nomenclature consolidée ; prix lus chez les distributeurs ; inventaire InvenTree ou Part-DB en option (Docker ou WSL2). **Jamais de commande passée par l'IA.** | Commande. |

### 7.3 Montage, essais, recalage
- **Notice de montage (`builddoc`, maison) :** étapes tirées de l'assemblage et des vues éclatées,
  une image par étape (`render_views`), pièces et vis à ajouter, couples de serrage ; sortie HTML ou
  PDF.
- **Essais réels (`benchtest`, maison) :**
  - procédure en YAML : montage, étapes, limites d'acceptation liées aux numéros `require` ;
  - mesures enregistrées en MCAP ou CSV (sigrok-cli, PyVISA, journaux ArduPilot ou ROS lus par
    rosbags) ;
  - verdict OK / FAIL.
- **Recalage (`calib`, maison) :** les paramètres du modèle (raideur, frottement, masse, coefficients
  hydrodynamiques, constantes moteur) sont ajustés aux mesures (SysIdentPy, python-control,
  scipy.optimize). Les écarts restants sont rapportés, puis la boucle repart.
- **Conversions vérifiées (`convert_check`, maison) :** aller-retour URDF ↔ MJCF ↔ SDF ↔ glTF, avec
  comparaison des masses, inerties et dimensions. Exemple de piège : la conversion de MuJoCo supprime
  en silence certains liens fixes.
- **Versionnage :**
  - git + git-lfs pour FCStd, STEP, STL et 3MF ;
  - comparaison des FCStd par `fcinfo` ou DiffCAD ;
  - KiCad en texte simple.

---

## 8. Installation en une commande, et formats d'échange

### 8.1 Pourquoi pixi
pixi est libre (BSD-3) et s'installe sans droits administrateur. Il crée des environnements par
projet et un `pixi.lock` qui fixe **l'empreinte sha256 de chaque paquet**, à la fois pour conda-forge,
RoboStack (ROS 2) et PyPI.

Les autres options et pourquoi elles ne suffisent pas :
- **uv :** PyPI seulement, donc pas de CalculiX, d'Elmer ni de ROS.
- **winget :** demande souvent les droits administrateur et n'a pas de verrou par projet.
- **Docker Desktop :** n'est pas libre (conditions d'abonnement) et demande les droits administrateur.

### 8.2 La commande
```
git clone https://github.com/NikeTheBee/mcp-freecad-v2
cd mcp-freecad-v2
powershell -ExecutionPolicy Bypass -File install\install.ps1 -Packs socle,marin,elec
```
1. Télécharge une version figée de pixi et vérifie son SHA256.
2. Lance `pixi install --locked` : les environnements des packs choisis, exactement ceux du verrou.
3. Télécharge les outils hors conda listés dans `tools.lock.json` (FreeCAD, Elmer, SU2, KiCad,
   PrusaSlicer, binaires SITL, XFOIL/AVL/QPROP, BELLHOP, GMAT...), en vérifiant le SHA256 et la
   signature quand il y en a une.
4. Lance `bootstrap.py`, qui enregistre les serveurs MCP et pose les skills des packs.
5. Lance les cas de référence des packs. Un outil qui échoue est marqué « non validé » et reste
   inutilisable.

On évite volontairement `irm … | iex` (exécuter un script directement depuis Internet).
`-Packs tout` installe tous les packs natifs.

### 8.3 Packs (tailles estimées)

| Pack | Contenu principal | Taille |
|---|---|---|
| `socle` | FreeCAD 1.1.4, serveurs MCP, CalculiX étendu, Gmsh, Netgen, NGSolve, Elmer, PyVista, Optuna/SALib/BoTorch, pyLife, MuJoCo, python-control | ~5 Go |
| `fluides` | SU2, CfdOF + blueCFD-Core (OpenFOAM 12), ParaView | ~4 Go |
| `air` | AeroSandbox, NeuralFoil, XFOIL, AVL, QPROP, JSBSim, ArduPilot SITL, pymavlink, RotorPy | ~1 Go |
| `fusee` | RocketPy, OpenRocket + Java, orlab, CEA, CoolProp, Cantera, pygasflow | ~1 Go |
| `espace` | Basilisk, Orekit, GMAT, pyViewFactor, SpacePy | ~1.5 Go |
| `terre` | PyChrono, Pinocchio, Pink, mink, CasADi, OpenCV, Open3D, PCL, RTAB-Map | ~3 Go |
| `soussol` | OpenGeoSys, Kratos GeoMechanics, gprMax | ~1.5 Go |
| `marin` | Capytaine, MoorDyn, Gazebo Jetty, ArduSub SITL, PythonVehicleSimulator, arlpy + BELLHOP | ~3 Go |
| `elec` | KiCad 10, ngspice, spicelib, PyBaMM, Magpylib, GetDP, FEMMT, scikit-rf, WireViz + Graphviz | ~3 Go |
| `embarque` | PlatformIO, Arduino CLI, Zephyr SDK, ESP-IDF, Arm GNU, OpenOCD, Renode, sigrok-cli, PyVISA | ~6 Go |
| `fab` | PrusaSlicer, CAMotics, sparrow, SheetMetal, Fasteners, freecad.gears, InteractiveHtmlBom, lib3mf | ~1 Go |
| `ros` (option) | ROS 2 Lyrical, Nav2, MoveIt, ros2_control ; Visual Studio 2022 Build Tools exigés | ~13 Go |
| `wsl` (option) | Ubuntu : OpenFOAM v2606, PX4, Stonefish, POSIM, DEM-Engine, Yade, crocoddyl/TSID, preCICE, JAX | ~25 Go |

### 8.4 Formats d'échange : ce qui rend les outils compatibles

| Donnée | Format | Contrôle |
|---|---|---|
| Géométrie | STEP (AP214/AP242) | relu et comparé au solide (existant) |
| Maillage, impression | 3MF (lib3mf), STL | idem |
| Robot | URDF, MJCF, SDF | `convert_check` |
| Vue 3D légère | glTF | validateur Khronos |
| Champs de résultats | VTU / VTK, HDF5 / XDMF | min, max, position du max, unités |
| Modèle système | FMU (FMI 3.0) | FMPy |
| Résultats et journaux | JSON, CSV, MCAP | schéma fixe |
| Exigences | Doorstop (YAML dans git) | matrice de traçabilité |
| Unités | SI partout entre outils, mm dans la CAO | pint 0.25 côté FreeCAD (0.26 exige Python 3.12) |

### 8.5 Plug and play : plugin Claude Code
Le dépôt devient aussi une *marketplace* :

```
claude plugin marketplace add NikeTheBee/mcp-freecad-v2
claude plugin install freecad-atelier@mcp-freecad-v2
```

- **Ce que porte le plugin :** les skills des packs, la déclaration des serveurs MCP et des hooks.
- **Ce qu'il ne porte pas :** les binaires de plusieurs gigaoctets. `tools_status` dit ce qui manque et
  donne la commande `install.ps1` à lancer.
- **Autres clients :** les serveurs MCP restent utilisables depuis les autres clients compatibles
  (Claude Desktop, VS Code, LM Studio...).

---

## 9. Validation : un cas de référence par physique

| Physique | Cas | Valeur attendue |
|---|---|---|
| Statique | NAFEMS LE1 / LE10 | σyy(D) = 92.7 MPa / −5.38 MPa |
| Contrainte thermique | NAFEMS LE11 | σzz(A) = −105 MPa |
| Modal | NAFEMS FV52 | f1 = 45.897 Hz |
| Flambage | Colonne d'Euler ; coque NASA SP-8007 | π²EI/(KL)² ; valeurs publiées |
| Thermique | NAFEMS T4 | T(E) = 18.25 °C |
| Thermomécanique | Bilame (Timoshenko) | formule exacte |
| Fluide lent | Poiseuille ; Schäfer-Turek 2D-1 | profil exact ; Cd = 5.5795, Cl = 0.0106 |
| Compressible | Cas NASA TMR (SU2) | données publiées |
| Hydrostatique | Boîte, cylindre, sphère ; recoupement Capytaine | formules exactes |
| Hydrodynamique | Masse d'eau ajoutée d'une sphère ; coefficients REMUS100 | ½ρV ; valeurs publiées |
| Thermochimie | CEA, exemples RP-1311 | tables NASA |
| Vol de fusée | RocketPy, vols de référence publiés | apogée à ±2 % |
| Aile | XFOIL / NeuralFoil sur NACA 0012 | polaires de référence |
| Robot | MuJoCo : pendule ; Pinocchio : RNEA contre `mj_inverse` | formule exacte ; accord à 1e-6 |
| Terrain | `terramech` contre Chrono SCM | écart borné |
| Sol, roche | Exemples de référence OpenGeoSys | données publiées |
| Orbite | Basilisk et Orekit : orbite képlérienne, période | formule exacte |
| Circuit | Filtre RC ; diviseur ; convertisseur abaisseur idéal | formules exactes |
| Batterie | PyBaMM : modèle SPM contre DFN, décharge à C/10 | cohérence publiée |
| Magnétisme | Magpylib contre Elmer : solénoïde | B = μ0·n·I |
| Fatigue | Rainflow ASTM E1049 | exemple publié |

Ces cas rejoignent `install/run_all_tests.py`. Ils tournent à chaque installation et après chaque mise
à jour d'un outil.

---

## 10. Sécurité

### 10.1 Informatique
1. **Mettre FreeCAD à jour en premier.** La 1.1.1 installée a des failles publiées. Vérifié le
   2026-11-14 dans les avis GitHub de FreeCAD :
   - exécution de code à l'ouverture d'un FCStd piégé (CVE-2026-34789, CVE-2026-34398,
     CVE-2026-34399), corrigée en 1.1.2 ;
   - écriture de fichiers arbitraires (CVE-2026-73234, corrigée en 1.1.2 ; deux avis corrigés en
     1.1.4) ;
   - XXE (CVE-2026-73235).

   Cible : **1.1.4**. Une faille de chargement de DLL n'est corrigée qu'en 26.3, encore en version
   candidate : il faudra la tester quand elle sortira.
2. **Tout est figé par empreinte :** `pixi.lock`, `tools.lock.json` (SHA256), signature Authenticode
   vérifiée quand l'éditeur signe. Pour les binaires sans empreinte publiée (SITL ArduPilot et
   ArduSub, GMAT), nous relevons et figeons la nôtre.
3. **Les adaptateurs sont isolés :**
   - lancés avec `python -I`, sans shell ;
   - dossier de travail dans le projet (`safe_out_path`) ;
   - délai maximal et limite mémoire ;
   - **pas de réseau par défaut**. Exceptions déclarées et mises en cache : ThrustCurve, météo,
     données d'orientation de la Terre pour Orekit.
4. **Les ports :** boucle locale uniquement. Pour SITL, rosbridge et Gazebo, l'adaptateur vérifie
   l'adresse d'écoute et refuse toute autre interface.
5. **Fichiers venus d'ailleurs** (FCStd, 3MF, modèles de robots, circuits avec `.include`) : ils sont
   traités comme du code et ouverts dans un processus isolé, avec en option le Bac à sable Windows.
6. **Licences :**
   - Les outils GPL et AGPL tournent comme programmes séparés, jamais liés à notre code. Ils sont
     téléchargés depuis leur source officielle, jamais redistribués.
   - Les données sous licence (ThrustCurve, UIUC, T200, modèles SPICE des fabricants) restent en cache
     local avec leur licence notée.

### 10.2 Monde physique : règles que l'IA applique comme des exigences
- **Aucune commande vers le réel.** Pas de liaison MAVLink vers un vrai véhicule, pas de pont ROS
  vers un vrai robot, pas de commande vers une imprimante ou une machine, pas de chargement de
  microprogramme sur une vraie carte, pas de commande d'achat. La simulation (SITL, émulation) est la
  seule cible.
- **Jamais de G-code machine écrit par l'IA** (`skill-cam` reste coupé). Lire celui de l'humain pour
  le vérifier est permis.
- **Secteur et haute tension** (> 50 V alternatif ou > 120 V continu) :
  - pas de conception côté secteur ;
  - alimentations certifiées du commerce seulement ;
  - le projet est marqué « personne qualifiée requise ».
- **Par défaut, très basse tension de sécurité** (≤ 60 V continu). Au-delà : isolation, verrouillage,
  et arrêt d'urgence matériel indépendant du microprogramme.
- **Batteries lithium :**
  - un BMS (surveillance cellule par cellule : tension, courant, température) et un fusible près de la
    batterie ;
  - les limites viennent uniquement de la fiche technique de la cellule ;
  - le transport suit UN38.3.
- **Fort courant :**
  - chaque branche a son fusible, dimensionné sous l'intensité admissible du câble ;
  - marge de déclassement ≥ 25 % et chute de tension vérifiée ;
  - précharge au-delà d'environ 24 V, protection contre l'inversion de polarité.
- **Sous l'eau :**
  - très basse tension de sécurité seulement ;
  - connecteurs IP68 ;
  - contrôleur d'isolement ou disjoncteur différentiel sur tout câble relié à la rive ;
  - essai en pression au-delà de la profondeur maximale, avant toute mise à l'eau.
- **Pression, énergie stockée, pièces en rotation :** le coefficient de sécurité est une exigence
  `require`, et la procédure d'essai réel exige protections et distance.
- **L'IA ne dit jamais « sûr » ni « certifié ».** Une valeur sans source (fiche technique, norme
  citée) est `UNKNOWN` et passe par un humain avant toute mise sous tension ou mise à l'eau.

---

## 11. Scénarios

| | **A. Natif** (recommandé pour commencer) | **B. A + WSL2** | **C. A + ROS 2 complet** |
|---|---|---|---|
| **Droits administrateur** | non | oui, une fois (WSL est déjà présent sur la machine) | non, sauf l'installeur Visual Studio |
| **Couverture** | ~85 % : tout le socle et les huit packs natifs | + OpenFOAM complet, PX4, Stonefish, DEM sur carte graphique, crocoddyl/TSID, preCICE, apprentissage | + Nav2, MoveIt, ros2_control, chaîne ROS de bout en bout |
| **Taille** | 10 à 35 Go selon les packs | + ~25 Go | + ~13 Go |

« Environnement complet » = A + B + C. A est installé et validé d'abord ; B et C s'y ajoutent sans le
remplacer.

---

## 12. Phases : ordre de construction

Chaque phase se termine quand ses cas de référence passent, que la suite complète reste verte, et
qu'un exemple réel a fait tout le tour de la boucle.

| Phase | Contenu | Taille |
|---|---|---|
| **0. Fondations** | FreeCAD 1.1.4 et nouvelle passe de tests. pixi + `pixi.lock` + `install.ps1` (pack `socle`). `adapters/` et `modules/` avec le lanceur (sous-processus, délai, JSON, cache). Outils `simulate`, `job`, `tools_status` ; `study` minimal (hypothèses + journal). Correction des bugs d'assemblage encore ouverts : repère des opérations Part dans un conteneur déplacé, répétition PartDesign hors pièce. | L |
| **1. Socle physique** | CalculiX : pression, gravité, rotation, modal, flambage, thermique, dilatation, plasticité, contact ; pyLife ; Elmer ; NGSolve ; MuJoCo ; GIF de mécanisme. Cas NAFEMS. | L |
| **2. Électrique et embarqué** | spicelib/ngspice, KiCad (`kicad_gate`), PyBaMM, Magpylib, WireViz, `wire_sizing`, `power_budget` ; PlatformIO + Renode. Règles de sécurité électrique actives. | M |
| **3. Du modèle au réel** | `dfm`, `tolstack`, `stdparts`, `builddoc`, `benchtest`, `calib`, `convert_check` ; PrusaSlicer, CAMotics, SheetMetal, Fasteners. | M |
| **4. Premier pack de domaine** | Celui du premier vrai projet (marin, terre, air, fusée, espace, sous-sol). | M |
| **5. Packs suivants** | Un par un, chacun validé avant le suivant. | M chacun |
| **6. Optimisation et traçabilité** | `study` complet : Optuna/pymoo/BoTorch, SALib, beso ; Doorstop ; dossier de justification. | M |
| **7. Voies B et C** | WSL2, puis ROS 2, si choisis. | M |
| **8. Plug and play** | Plugin et marketplace Claude Code, `-Packs tout`, documentation, CI. | S |

---

## 13. Décisions à prendre (propriétaire)

1. **Le scénario :** A, puis B et C (« complet ») ? Ou A seulement pour commencer ?
2. **FreeCAD 1.1.4 tout de suite** (recommandé, sécurité), avec toute la suite repassée dessus.
3. **Le premier vrai projet** (n'importe lequel) : il fixe le pack de la phase 4.
4. **Estimation d'impression :** autoriser l'IA à lancer PrusaSlicer pour obtenir le temps et la
   matière ? Cela génère un G-code temporaire, supprimé, jamais envoyé. Par défaut : **non**.
5. **Licences à accepter comme programmes séparés :** AGPL (OpenModelica, KiBot, Part-DB),
   BSD-4 (Kratos), NOSA (OpenVSP, 42). Ou les écarter ?
6. **Moteur-fusée :** confirmer le périmètre (thermochimie, tuyère, refroidissement, structure d'un
   moteur liquide ou hybride ; propergol solide exclu).

---

## 14. Outils écartés et pourquoi

| Outil | Raison |
|---|---|
| Isaac Sim / Isaac Lab, OceanSim | Cœur (Kit SDK) et ressources propriétaires |
| HoloOcean, Project AirSim, Cosys-AirSim | Dépendent d'Unreal (licence non libre) |
| FluidX3D | Licence non libre (interdit l'usage commercial, militaire et l'entraînement d'IA) |
| FEMM (et donc pyleecan) | Licence Aladdin, non libre ; pyleecan exige Python < 3.11 |
| LTspice, QSPICE, STM32CubeCLT, ODrive Pro/S1 | Propriétaires ou micrologiciel fermé |
| RASAero II, RPA, SPENVIS, AE9/AP9 | Fermés ou web seulement (recoupement à la main seulement) |
| Docker Desktop | Non libre, demande les droits administrateur |
| AirSim, Colosseum, UUV Simulator, Plankton, LIGGGHTS, poliastro/hapsira | Gelés, archivés ou abandonnés |
| gym-pybullet-drones, PyBullet, Brax, Webots | Pas de version Windows propre, ou projet à l'arrêt |
| humanoid-gym, legged_gym | Isaac Gym abandonné ; humanoid-gym sans licence |
| RobotCAD | Exige Python 3.12 dans FreeCAD (1.1 a 3.11) ; conflit d'espace de noms avec CROSS |
| FreeCAD Ship | Non maintenu, cassé par les changements d'unités : hydrostatique maison |
| OpenProp | MATLAB seulement : on reprend sa théorie dans `thruster` |
| SuperSlicer, ToOptix, SUAVE, FAST-UAV, PySpice, KiBoM | Arrêtés, archivés ou dépassés |
| Dépôts de tuyère ou refroidissement sans licence (evand/nozzle, lrekit, AeroMOC, THERMAT) | Sans licence = aucun droit d'utilisation |
| Serveurs MCP tiers (imprimantes, MAVLink, rosbridge, ParaView, Blender, astrodynamique en ligne…) | Exécutent du code, commandent du matériel sans authentification, ou envoient les données en ligne |
| `freecad` de conda-forge | Instantanés hebdomadaires, pas une version stable |
| Mettre à jour l'atelier Rocket vers 5.1.3 | Exige numpy ≥ 2.4.1, absent du Python de FreeCAD 1.1 |
| Commande automatique de pièces | Engage de l'argent : l'humain commande |

---

## Annexe : versions relevées le 2026-11-14

À revérifier à l'installation : l'installeur fige la version exacte et son empreinte.

| Pack | Outil | Version | Licence |
|---|---|---|---|
| socle | FreeCAD | 1.1.4 (26.3 en version candidate) | LGPL-2.1+ |
| socle | CalculiX / Gmsh / Netgen | 2.23 / 4.15.2 / 6.2.2608 | GPL-2.0+ / GPL-2.0+ / LGPL-2.1 |
| socle | Elmer / NGSolve | 26.2.1 / 6.2.2608 | GPL-2.0+ (bibliothèques LGPL) / LGPL-2.1 |
| socle | MuJoCo (+ Menagerie) | 4.0.1 | Apache-2.0 |
| socle | PyVista / VTK | 0.49.2 / 9.7.2 | MIT / BSD-3 |
| socle | pyLife / FLife | 2.3.1 / 3.0.1 | Apache-2.0 / MIT |
| socle | Optuna / pymoo / SALib / BoTorch / SMT | 5.0.0 / 0.6.2 / 1.6.0 / 0.18.1 / 2.15.1 | MIT / Apache-2.0 / MIT / MIT / BSD-3 |
| socle | python-control / CasADi / SysIdentPy | 0.10.2 / 3.8.2 / 0.9.0 | BSD-3 / LGPL-3.0+ / BSD-3 |
| fluides | SU2 / CfdOF / blueCFD-Core / ParaView | 8.6.0 / 1.37.4 / 2024-1 / 6.2.1 | LGPL-2.1 / LGPL-3.0 / GPL-3.0 / BSD-3 |
| air | AeroSandbox / NeuralFoil / JSBSim | 4.2.10 / 0.3.3 / 1.3.3 | MIT / MIT / LGPL-2.1+ |
| air | XFOIL / AVL / QPROP | 6.99 / 3.52 / 1.22 | GPL |
| air | ArduPilot SITL / pymavlink / RotorPy | Copter 4.7.0 / 2.4.50 / 3.0.0 | GPL-3.0 / LGPL-3.0 / MIT |
| fusée | RocketPy / OpenRocket + orlab | 1.13.0 / 24.12 + 0.10.0 | MIT / GPL-3.0+ + GPL-2.0 |
| fusée | NASA CEA / CoolProp / Cantera / pygasflow | 3.3.4 / 8.0.0 / 3.2.0 / 1.4.1 | Apache-2.0 / MIT / BSD-3 / GPL-3.0 |
| espace | Basilisk / orekit_jpype / GMAT | 2.12.2 / 13.1.9.0 / R2026a | ISC / Apache-2.0 / Apache-2.0 |
| terre | PyChrono / Pinocchio / Pink / mink | 10.0.0 / 4.1.0 / 4.4.0 / 1.4.0 | BSD-3 / BSD / Apache-2.0 / Apache-2.0 |
| terre | OpenCV / Open3D / PCL / RTAB-Map | 5.1.0 / 0.20.0 / 1.15.1 / 0.23 | Apache-2.0 / MIT / BSD-3 / BSD-3 |
| sous-sol | OpenGeoSys / Kratos / gprMax | 6.5.9 / 10.4.4 / 4.0.1 | BSD modifiée / BSD-4 / GPL-3.0+ |
| marin | Capytaine / MoorDyn | 3.0.0 / 2.7.2 | Apache-2.0 / BSD-3 |
| marin | Gazebo Jetty / ArduSub SITL / PythonVehicleSimulator / arlpy | conda-forge / version continue / commit figé / 1.9.3 | Apache-2.0 / GPL-3.0 / MIT / BSD-3 |
| élec | KiCad / ngspice / spicelib | 10.0.8 / 47 / 1.6.4 | GPL-3.0+ / BSD-3 / GPL-3.0 |
| élec | PyBaMM / Magpylib / GetDP / FEMMT | 26.10 / 6.3.0 / 3.5.0 / 0.5.4 | BSD-3 / BSD-3 / GPL-2.0+ / GPL-3.0 |
| élec | openEMS / scikit-rf / WireViz | 0.37.0 / 2.2.0 / 0.4.1 | GPL-3.0 / BSD-3 / GPL-3.0 |
| embarqué | PlatformIO / Renode / Zephyr SDK / ESP-IDF | 6.2.0 / 1.17.0 / 1.0.1 / 6.1 | Apache-2.0 / MIT / Apache-2.0 / Apache-2.0 |
| embarqué | sigrok-cli / PyVISA | 0.7.2 (+ versions nocturnes) / 1.16.2 | GPL-3.0 / MIT |
| fab | PrusaSlicer / CAMotics / SheetMetal / Fasteners | 2.9.7 / 1.2.0 / 0.8.27 / 0.6.00 | AGPL-3.0 / GPL-2.0+ / LGPL-2.1+ / LGPL-2.1 (à confirmer) |
| fab | InteractiveHtmlBom / lib3mf / git-lfs | 2.12.0 / 2.5.0 / 3.8.1 | MIT / BSD-2 / MIT |
| traçabilité | Doorstop | 3.2 | LGPL-3.0 |
| outillage | pixi | 0.84.2 | BSD-3 |
