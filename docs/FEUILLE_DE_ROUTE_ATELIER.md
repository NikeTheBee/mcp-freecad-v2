# Atelier d'ingénierie local : feuille de route simulation, test virtuel, fabrication

> Rédigé le 2026-11-14, à partir d'une recherche sur les outils libres. Versions et licences relevées
> ce jour-là sur les sites officiels, GitHub, PyPI et conda-forge.
> **Rien de ce qui suit n'est encore installé ni testé ici.** Un outil n'entre dans le projet qu'après
> avoir refait son cas de référence (§8). L'installeur figera les versions exactes et leurs empreintes.
>
> **Statut : proposition.** Elle attend les choix du propriétaire (§12).

---

## 1. Ce qui est demandé

- **Des projets « frontière » :** une fusée (le véhicule, et un moteur de niveau étudiant), un drone, un
  robot humanoïde.
- **Une boucle complète :** besoin → hypothèses → modélisation → simulation → test virtuel →
  résultats → correction. On recommence jusqu'à ce que tout passe, puis on fabrique : impression 3D,
  usinage, découpe, circuits.
- **La physique visée :**
  - mécanique : statique, flambage, vibrations, fatigue, compression, contact ;
  - thermique : conduction, convection, rayonnement, dilatation ;
  - fluides : écoulement lent, supersonique, tuyère ;
  - dynamique et commande : vol, équilibre, marche ;
  - électronique et microprogramme.
- **Les contraintes :**
  - tout est gratuit, libre et local ;
  - sécurisé ;
  - le moins de tokens possible ;
  - une arborescence propre ;
  - les outils natifs Windows en priorité ;
  - l'installation tient en une commande, le tout se branche sans réglage (plug and play) ;
  - des skills poussés par domaine.

---

## 2. Principes : le pourquoi

| # | Principe | Pourquoi |
|---|---|---|
| P1 | **Un seul contrat pour tous les outils.** Chaque logiciel externe est enveloppé par un *adaptateur*. L'adaptateur valide les entrées, lance l'outil à part, puis renvoie un verdict `OK / FAIL / UNKNOWN` et quelques chiffres. Le journal complet reste sur le disque. | C'est ce qui rend le projet fiable aujourd'hui (`check`, `fem_static`) : l'IA lit une mesure et un verdict, elle ne croit pas un outil sur parole. C'est aussi ce qui économise les tokens. |
| P2 | **Aucun solveur sans cas de référence.** Avant d'être utilisé, un outil refait un cas dont la réponse est connue (NAFEMS, formule exacte). S'il s'en écarte, il est refusé : `UNKNOWN`. | Un solveur mal réglé donne une belle image fausse. Le cas de référence le prouve à chaque installation et à chaque mise à jour. |
| P3 | **Les environnements Python ne se mélangent pas.** FreeCAD 1.1 tourne sous Python 3.11 avec numpy 1.26 et VTK 9.3. La plupart des outils récents veulent numpy 2, Python 3.12 ou leur propre VTK. Chaque famille d'outils a donc son environnement, et les échanges passent par des fichiers : STEP, STL, URDF/MJCF, `.ork`, CSV, JSON. | Installer ces outils dans le Python de FreeCAD le casserait. Vérifié : l'atelier Rocket 5.1.3 exige déjà numpy ≥ 2.4.1, et FreeCAD 1.1.1 a numpy 1.26.4. |
| P4 | **Natif Windows d'abord, WSL2 en option.** | WSL2 demande des droits administrateur et double les environnements. On ne l'utilise que pour ce qui n'existe pas nativement (§10). |
| P5 | **Sécurité par défaut** (§9). Aucun port réseau sauf la boucle locale, aucun serveur MCP tiers qui exécute du code. Jamais de G-code écrit par l'IA, jamais de commande vers une vraie machine. | Un modèle de langage, ou un fichier piégé, ne doit pas pouvoir agir sur le monde physique. |
| P6 | **Une greffe à la fois** (cahier des charges §11) : on installe, on valide, puis on passe à la suivante. | Quand tout arrive d'un coup, on ne sait plus ce qui casse. |
| P7 | **L'IA pilote, elle ne regarde pas défiler les itérations.** Les balayages et optimisations tournent seuls, en local, et l'IA ne reçoit que le résumé. | 200 itérations d'optimisation coûtent zéro token si l'IA n'est pas dans la boucle. |

---

## 3. Architecture cible : le comment

```
Utilisateur (langage naturel)
   └─ Claude Code
        ├─ MCP freecad          (existant) modélisation dans FreeCAD
        └─ MCP freecad-layers   (existant) contrat, check, export, fem_static...
              + simulate / job / study / tools_status   (NOUVEAUX)
                    │  lance en sous-processus, sans shell, avec un délai maximal et une limite mémoire
                    ▼
              server/adapters/<outil>/run.py   (dans l'environnement pixi de l'outil)
                    │  entrée JSON → outil → sortie JSON (verdict + chiffres + chemins)
                    ▼
              CalculiX · Elmer · SU2 · OpenFOAM · RocketPy · OpenRocket · CEA · AeroSandbox ·
              XFOIL/AVL/QPROP · JSBSim · ArduPilot SITL · MuJoCo · Pinocchio · ngspice · KiCad ·
              PrusaSlicer · CAMotics ...
```

### 3.1 Pourquoi pas un serveur MCP par outil
- **Le coût en tokens :** chaque serveur MCP ajoute ses descriptions d'outils à *chaque* session.
  Quatre outils génériques coûtent bien moins que soixante outils spécialisés.
- **La sécurité :** les serveurs MCP tiers relevés pour ces logiciels exécutent souvent du code
  arbitraire, écoutent sur le réseau sans authentification, ou peuvent armer un vrai drone (§13). On
  reprend leurs bonnes idées, pas leur code. Les meilleures : `ogive` pour OpenRocket (dossier racine
  imposé, reçus sha256) et `mcp-calculix` (pas de fichier d'entrée brut, boucle locale seulement).

### 3.2 Les quatre nouveaux outils MCP

| Outil | Rôle |
|---|---|
| `simulate(kind, target, params)` | Lance une simulation d'un type donné (`fem_modal`, `thermal`, `cfd_compressible`, `rocket_flight`, `drone_sitl`, `robot_balance`...). Renvoie le verdict et les chiffres, ou un numéro de tâche si le calcul est long. |
| `job(id, action)` | Suit une tâche longue : état, résultat, annulation. Une ligne par appel. |
| `study(...)` | La boucle d'étude (§3.4) : hypothèses, paramètres, critères, itérations, optimisation, journal. |
| `tools_status()` | Ce qui est installé, avec les versions, le résultat du cas de référence, et ce qu'il manque avec la commande pour l'installer. |

Le détail de chaque `kind` est dans le skill de son domaine, chargé seulement quand on en a besoin.

### 3.3 Contrat d'un adaptateur
```json
// entrée (écrite par freecad-layers dans le dossier de l'étude)
{"kind": "fem_modal", "geometry": "etude/piece.step", "params": {"modes": 6, "material": "aluminium"},
 "limits": {"min_freq_Hz": 80}, "timeout_s": 900}
// sortie (lue par freecad-layers, résumée en 3 à 10 lignes pour l'IA)
{"verdict": "OK", "summary": "f1 = 112.4 Hz (> 80 Hz) ; 6 modes ; maillage 2 mm, 41 k nœuds",
 "values": {"f1_Hz": 112.4}, "files": ["etude/fem_modal/result.vtu"],
 "tool": "calculix 2.23", "reference_case": "NAFEMS FV52 OK", "inputs_hash": "sha256:…"}
```
- **Le cache :** un même `inputs_hash` (géométrie + paramètres + version de l'outil) renvoie le résultat
  déjà calculé. Redemander ne coûte rien, et chaque résultat reste reproductible.
- **Pas de shell :** les exécutables sont appelés avec une liste d'arguments. Les scripts envoyés sur
  l'entrée standard (XFOIL, AVL) sont générés par l'adaptateur, jamais copiés tels quels d'une donnée.

### 3.4 La boucle d'étude (`study`)
```
exigences (require) ─┐
hypothèses ──────────┤   charges, matériau, vent, températures... chacune écrite et datée
paramètres ──────────┤   cellules du tableur FreeCAD (épaisseur, envergure, diamètre...)
                     ▼
   modèle → check (géométrie + contrat) → simulate × N → critères → verdict de l'itération
                     │                                        │
                     │  FAIL : correction                     │  tout OK : record_step + recipe
                     │   - l'IA propose (raisonnement), ou    ▼
                     │   - balayage / optimisation locale (Optuna, pymoo), sans l'IA
                     └── checkpoint avant chaque essai, restore si c'est pire
```
- **Le journal :** une ligne par itération (paramètres → résultats → verdict). Il produit à la fin un
  **dossier de justification** : exigences, hypothèses, résultats, versions des outils, cas de
  référence. C'est utile pour un projet de fusée qu'on doit faire accepter.
- **Ce que reçoit l'IA :** le meilleur jeu de paramètres, les critères limitants et la sensibilité
  (SALib). Pas les 200 itérations.

### 3.5 Arborescence
```
MCP FREECAD/
├─ pixi.toml · pixi.lock          environnements verrouillés (sha256 de chaque paquet)
├─ install/
│   ├─ install.ps1                LA commande unique (§7)
│   ├─ bootstrap.py               existant, étendu par profils
│   └─ tools.lock.json            outils hors conda : URL officielle + SHA256 + licence
├─ server/
│   ├─ freecad-mcp/               base (existant)
│   ├─ freecad_layers/            couches (existant) + sim.py (orchestrateur) + study.py
│   ├─ adapters/                  NOUVEAU : un dossier par outil (run.py, README, cas de référence)
│   └─ layers_mcp/                outils MCP (existant) + simulate / job / study / tools_status
├─ skills/                        existants + nouveaux (§3.6)
├─ validation/                    NOUVEAU : cas de référence par physique (§8), lancés par run_all_tests
├─ tools/        (git-ignoré)     binaires téléchargés et vérifiés
├─ .pixi/        (git-ignoré)     environnements
└─ docs/
```
La racine d'installation se règle (`MCPFC_HOME`). Les environnements et le cache pixi suivent cette
racine : rien de lourd ne va sur le disque système s'il est plein.

### 3.6 Skills nouveaux ou étendus
Ils suivent la spécification Agent Skills : nom = dossier, moins de 500 lignes, chargés à la demande.
- **`skill-study` :** la boucle, les hypothèses, quand optimiser, comment lire une sensibilité.
- **`skill-structure` :** statique avec pression, gravité et rotation ; modal, flambage, plasticité,
  contact, fatigue.
- **`skill-thermal` :** conduction, convection, rayonnement, dilatation, contraintes thermiques.
- **`skill-cfd` :** quand prendre Elmer, OpenFOAM ou SU2 ; maillage ; convergence ; lecture des
  coefficients.
- **`skill-rocket`** (étendu) : vol 6 degrés de liberté, Monte-Carlo, récupération.
- **`skill-propulsion` :** CEA, tuyère, refroidissement, chambre sous pression.
- **`skill-drone`** (étendu) : hélice et moteur, SITL ArduPilot, réglage des boucles de commande.
- **`skill-humanoid` :** URDF vers MJCF, actionneurs, équilibre (ZMP, capture point), marche,
  dimensionnement des moteurs.
- **`skill-control` :** PID, LQR, MPC (python-control, CasADi).
- **`skill-electronics` :** ngspice, KiCad, PlatformIO, Renode.
- **`skill-fabrication` :** réunit les règles impression 3D, usinage, découpe et circuits imprimés
  (§6).

Option : un sous-agent *vérificateur indépendant* qui refait les vérifications d'une étude sans
connaître le raisonnement qui l'a produite.

---

## 4. Couverture par physique

| Physique | Outil principal (natif Windows) | Recoupement | État aujourd'hui |
|---|---|---|---|
| Statique linéaire : force, **pression, gravité, rotation** | CalculiX (fourni avec FreeCAD) | MYSTRAN, formules | forces seules → à étendre |
| Flambage | CalculiX `buckling` (nouveau dans 1.1) | Euler π²EI/(KL)² | à ajouter |
| Vibrations, modes propres | CalculiX `frequency` | NAFEMS FV52 | à ajouter |
| Thermique (conduction, convection, rayonnement, transitoire) | CalculiX `thermomech` | Elmer, NAFEMS T4 | à ajouter |
| **Dilatation**, contraintes thermiques | CalculiX thermomécanique couplé | NAFEMS LE11, bilame | à ajouter |
| Plasticité, grandes déformations, contact | CalculiX non linéaire | plaque trouée, Hertz | à ajouter |
| Compression de mousse, hyperélasticité | CalculiX par injection dans le fichier `.inp` | essai analytique | plus tard |
| Fatigue | pyLife (courbes S-N, Miner, rainflow) sur les contraintes CalculiX | ASTM E1049 | à ajouter |
| Fluide lent (drone, aile, refroidissement) | Elmer *Flow* (simple) ; OpenFOAM via CfdOF (RANS) | Poiseuille, cavité de Ghia, Schäfer-Turek | absent |
| Fluide **compressible / supersonique** (fusée, tuyère) | **SU2** | cas NASA TMR | absent |
| Thermique fluide + solide (tuyère, électronique) | Elmer *Heat + Flow* | SU2 multizone | absent |
| Thermochimie d'un moteur-fusée | **NASA CEA** (libre, Apache-2.0, depuis 12/2025) | RocketCEA | absent |
| Profil de tuyère | pygasflow (Rao), puis vérification SU2 | — | absent |
| Refroidissement régénératif 1D | module maison (Bartz) + CoolProp | méthode de référence | absent |
| Vol de fusée 6 DDL + Monte-Carlo | **RocketPy** | OpenRocket (via orlab) | Barrowman seul |
| Aérodynamique d'aile et d'hélice | AeroSandbox + NeuralFoil, XFOIL, AVL, QPROP | — | formules simples |
| Dynamique de vol d'un avion | JSBSim | — | absent |
| Vol de drone avec le vrai pilote automatique | **ArduPilot SITL** (binaires Windows) + pymavlink | PX4 SIH (WSL2) | absent |
| Commande : PID, LQR, MPC | python-control, CasADi | — | absent |
| Dynamique d'un robot, équilibre, marche | **MuJoCo 4** | Pinocchio (conda-forge) | URDF seulement |
| Cinématique inverse | mink (MuJoCo), Pink (Pinocchio) | SymPy (forme exacte) | absente |
| ROS 2 (MoveIt, ros2_control) | ROS 2 Lyrical via RoboStack (option) | — | génération seule |
| Circuits | ngspice | — | absent |
| Circuit imprimé | KiCad 10 (`kicad-cli`, rapports DRC en JSON) | — | absent |
| Microprogramme sans carte | PlatformIO + Renode / QEMU Espressif | — | absent |
| Système multi-domaine (moteur + réducteur + batterie) | OpenModelica (licence AGPL) / FMPy | — | option |
| Optimisation, sensibilité | Optuna, pymoo, SALib | — | absent |
| Optimisation topologique | beso (CalculiX) | — | option |
| Post-traitement numérique | PyVista / VTK (dans leur environnement) | ParaView `pvbatch` | — |
| **Animation** | FreeCAD : GIF d'un mécanisme sur sa course ; MuJoCo : vidéo hors écran ; RocketPy : trajectoire | — | absente |

**Pas faisable nativement sous Windows** (seulement en WSL2, §10) :
- couplage fluide-structure par preCICE ;
- solveurs OpenFOAM compilés sur mesure ;
- gros calculs MPI ;
- crocoddyl, TSID, Placo ;
- apprentissage par renforcement JAX sur carte graphique.

---

## 5. Par projet : ce que la boucle saura faire

### 5.1 Fusée
- **Exigences :** apogée visée, marge statique de 1 à 2 calibres, vitesse en sortie de rampe ≥ 15 m/s,
  descente ≤ 5 m/s, zone d'atterrissage.
- **Hypothèses :** moteur commercial certifié (courbe de poussée ThrustCurve, mise en cache avec sa
  licence), vent de 0 à 8 m/s, angle de rampe, masses réelles (nomenclature).
- **Modèle :** atelier Rocket (ogive, tube, ailerons, bagues) et pièces PartDesign.
- **Simulations :**
  - centre de pression par Barrowman et OpenRocket ;
  - RocketPy en 6 DDL, 500 tirs Monte-Carlo ;
  - flottement des ailerons (calculateur de l'atelier) ;
  - contraintes dans les ailerons et le support moteur (CalculiX) ;
  - traînée supersonique avec SU2 si nécessaire.
- **Tests virtuels :** marge statique en sortie de rampe, accélération maximale, ellipse
  d'atterrissage, ouverture du parachute.
- **Moteur (liquide ou hybride, niveau étudiant) :**
  - CEA donne l'impulsion spécifique, la température et les gaz de chambre ;
  - tuyère Rao (pygasflow), vérifiée avec SU2 ;
  - refroidissement 1D (Bartz + CoolProp) ;
  - chambre sous pression et chaleur avec CalculiX thermomécanique.
- **Hors périmètre :** composition et fabrication de propergol solide ; tout usage d'arme.
- **Fabrication :** ogive et ailerons imprimés (3MF), bagues découpées (DXF), tuyère usinée (STEP +
  plan, programmée par un humain).

### 5.2 Drone
- **Exigences :** masse, charge utile, autonomie, rapport poussée/poids ≥ 2, réponse de l'assiette.
- **Simulations :**
  - dimensionnement AeroSandbox ;
  - hélice et moteur avec QPROP (données UIUC citées, jamais redistribuées) ;
  - bras en statique, en modal (éloigner leurs résonances de la rotation des moteurs) et en fatigue ;
  - pour une aile : XFOIL, AVL (stabilité), JSBSim.
- **Tests virtuels :** vol stationnaire, réponse à un échelon, rafale de vent et mission complète en
  **ArduPilot SITL**, avec le vrai code du pilote automatique. Réglage des PID avec python-control.
- **Fabrication :** châssis imprimé ou plaque de carbone découpée (DXF), circuit de distribution
  (KiCad).

### 5.3 Robot humanoïde
On le construit par sous-ensembles : une jambe, puis les deux jambes avec le bassin, puis le haut du
corps.
- **Chaîne :** FreeCAD → URDF (existant, inerties en SI) → MJCF avec actionneurs → MuJoCo.
- **Simulations :**
  - enveloppe couple-vitesse de chaque articulation sur les mouvements prévus (`mj_inverse`), puis
    choix des moteurs et réducteurs (couple efficace, échauffement) ;
  - résistance des supports de servomoteurs (CalculiX) ;
  - cinématique inverse (mink).
- **Tests virtuels :**
  - tenir debout ;
  - encaisser une poussée ;
  - marcher selon un module d'équilibre maison (ZMP, capture point), validé sur le modèle G1 de MuJoCo
    Menagerie.
- **Animation :** vidéo hors écran avec MuJoCo.
- **Mécatronique :** carte de commande (KiCad), microprogramme SimpleFOC testé sous Renode.
- **ROS 2 :** paquet généré (existant) ; MoveIt et ros2_control via RoboStack en option.

---

## 6. Fabrication

| Procédé | Ce que fait l'IA | Ce que fait l'humain |
|---|---|---|
| Impression 3D | `printability` puis `export` 3MF vérifié. Option : projet PrusaSlicer (`--export-3mf`) avec **le profil de l'utilisateur**, sans G-code. | Découpe en tranches, vérifie l'aperçu, imprime. |
| Usinage | Livre le STEP et le plan TechDraw. **Lit** le G-code de l'humain avec CAMotics (`camsim` → STL de la pièce usinée), puis le compare au modèle avec `verify`. | Programme les trajectoires (FreeCAD CAM ou Kiri:Moto), puis les envoie (UGS, Candle). |
| Découpe laser, tôlerie | Déplie la pièce (SheetMetal) en DXF ; imbrication avec sparrow. | Découpe (LaserGRBL). |
| Circuit imprimé | Fichiers Gerber, perçage et nomenclature avec `kicad-cli` ; rapport DRC/ERC. | Commande chez le fabricant. |

**Règles qui ne changent pas :**
- L'IA n'écrit jamais de G-code machine. `skill-cam` reste coupé.
- L'IA n'envoie aucune commande à une imprimante, une fraiseuse, un drone ou un robot réel. La
  simulation (SITL) est la seule cible.
- Un projet 3MF ou FCStd venu d'ailleurs est traité comme du code. Un réglage `post_process`
  embarqué dans un 3MF peut lancer un programme (CVE-2023-47268). On découpe donc toujours avec ses
  propres profils.

---

## 7. Installation en une commande

### 7.1 Pourquoi pixi
pixi est libre (BSD-3) et s'installe sans droits administrateur. Il crée des environnements par
projet et un fichier `pixi.lock` qui fixe **l'empreinte sha256 de chaque paquet**, à la fois pour
conda-forge, RoboStack (ROS 2) et PyPI. C'est la seule option qui fait les trois en même temps.

Les autres options et pourquoi elles ne suffisent pas :
- **uv :** PyPI seulement, donc pas de CalculiX, d'Elmer ni de ROS.
- **winget :** demande souvent les droits administrateur et n'a pas de verrou par projet.
- **Docker Desktop :** n'est pas libre (conditions d'abonnement) et demande les droits administrateur.

### 7.2 La commande
```
git clone https://github.com/NikeTheBee/mcp-freecad-v2
cd mcp-freecad-v2
powershell -ExecutionPolicy Bypass -File install\install.ps1 -Profils base,fusee
```
1. Télécharge une version figée de pixi et vérifie son SHA256.
2. Lance `pixi install --locked` : les environnements, exactement ceux du verrou.
3. Télécharge les outils hors conda listés dans `tools.lock.json` (FreeCAD, Elmer, SU2, PrusaSlicer,
   KiCad, XFOIL/AVL/QPROP, ArduPilot SITL, CAMotics), en vérifiant le SHA256 et la signature quand il y
   en a une.
4. Lance `bootstrap.py`, qui enregistre les serveurs MCP et pose les skills.
5. Lance les cas de référence du profil. Un outil qui échoue est marqué « non validé » et reste
   inutilisable.

On évite volontairement `irm … | iex` (exécuter un script directement depuis Internet).

### 7.3 Profils (tailles estimées)

| Profil | Contenu | Taille |
|---|---|---|
| `base` | FreeCAD 1.1.4, serveurs MCP, CalculiX étendu, Gmsh, Netgen, PyVista, Optuna/SALib, pyLife | ~3 Go |
| `fusee` | RocketPy, OpenRocket + Java (conda-forge), orlab, CEA, CoolProp, Cantera, pygasflow, SU2 | ~1 Go |
| `drone` | AeroSandbox, NeuralFoil, XFOIL, AVL, QPROP, JSBSim, ArduPilot SITL, pymavlink, python-control | ~1 Go |
| `robot` | MuJoCo 4, Menagerie, Pinocchio, Pink, mink, CasADi, SymPy, yourdfpy | ~2 Go |
| `fluides` | Elmer 26, CfdOF + blueCFD-Core (OpenFOAM 12), ParaView | ~4 Go |
| `meca` (mécatronique) | KiCad 10, ngspice, PlatformIO, Renode | ~3 Go |
| `fab` (fabrication) | PrusaSlicer, CAMotics, sparrow, SheetMetal | ~0.5 Go |
| `ros` (option) | ROS 2 Lyrical (RoboStack), MoveIt, ros2_control, Gazebo Jetty ; Visual Studio 2022 Build Tools exigés | ~13 Go |
| `wsl` (option) | Ubuntu : OpenFOAM v2606, PX4 SIH, crocoddyl/TSID/Placo, preCICE, JAX sur carte graphique | ~20 Go |

### 7.4 Environnements pixi
- **`mcp` :** le pont actuel, qui remplace `~/.freecad-mcp/venv`.
- **`sim` (Python 3.12) :** structure, thermique, fluides, fusée, drone.
- **`robot` (Python 3.12) :** MuJoCo, Pinocchio, commande.
- **`ros` et `post`** (ParaView) : en option.

Des environnements plus petits veulent dire moins de conflits de versions.

### 7.5 Plug and play : plugin Claude Code
Le dépôt devient aussi une *marketplace* :

```
claude plugin marketplace add NikeTheBee/mcp-freecad-v2
claude plugin install freecad-atelier@mcp-freecad-v2
```

- **Ce que porte le plugin :** les skills, la déclaration des serveurs MCP et des hooks.
- **Ce qu'il ne porte pas :** les binaires de plusieurs gigaoctets. `tools_status` dit ce qui manque et
  donne la commande `install.ps1` à lancer.

---

## 8. Validation : un cas de référence par physique

| Physique | Cas | Valeur attendue |
|---|---|---|
| Statique | NAFEMS LE1 / LE10 | σyy(D) = 92.7 MPa / −5.38 MPa |
| Contrainte thermique | NAFEMS LE11 | σzz(A) = −105 MPa |
| Modal | NAFEMS FV52 | f1 = 45.897 Hz |
| Flambage | Colonne d'Euler | π²EI/(KL)² |
| Thermique | NAFEMS T4 | T(E) = 18.25 °C |
| Thermomécanique | Bilame (Timoshenko) | formule exacte |
| Fluide lent | Poiseuille ; Schäfer-Turek 2D-1 | profil exact ; Cd = 5.5795, Cl = 0.0106 |
| Compressible | Cas NASA TMR (SU2) | données publiées |
| Thermochimie | CEA, exemples RP-1311 | tables NASA |
| Vol de fusée | RocketPy, vols de référence publiés | apogée à ±2 % |
| Aile | XFOIL / NeuralFoil sur NACA 0012 | polaires de référence |
| Robot | MuJoCo : pendule et chute libre ; Pinocchio : RNEA contre `mj_inverse` | formule exacte ; accord à 1e-6 |
| Fatigue | Rainflow ASTM E1049 | exemple publié |

Ces cas rejoignent `install/run_all_tests.py`. Ils tournent à chaque installation et après chaque mise
à jour d'un outil.

---

## 9. Sécurité

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
   vérifiée quand l'éditeur signe.
3. **Les adaptateurs sont isolés :**
   - lancés avec `python -I`, sans shell ;
   - dossier de travail dans le projet (`safe_out_path`) ;
   - délai maximal et limite mémoire ;
   - **pas de réseau par défaut**. Exceptions déclarées et mises en cache : ThrustCurve, météo
     RocketPy.
4. **Les ports :** boucle locale uniquement. ArduPilot SITL sera vérifié en phase 3 : s'il écoute sur
   toutes les interfaces, l'adaptateur le refuse ou le confine.
5. **Fichiers venus d'ailleurs** (FCStd, 3MF, modèles de robots téléchargés) : ils sont traités comme
   du code et ouverts dans un processus isolé, avec en option le Bac à sable Windows.
6. **Monde physique :** SITL uniquement, aucune liaison MAVLink vers un vrai drone, aucun pont ROS vers
   un vrai robot, aucune commande vers une machine.
7. **Licences :**
   - Les outils GPL et AGPL tournent comme programmes séparés, jamais liés à notre code. Ils sont
     téléchargés depuis leur source officielle, pas redistribués dans le dépôt.
   - Les données sous licence (ThrustCurve, UIUC) sont mises en cache localement, jamais republiées.

---

## 10. Scénarios

| | **A. Natif léger** (recommandé pour commencer) | **B. A + WSL2** | **C. A + ROS 2 complet** |
|---|---|---|---|
| **Droits administrateur** | non | oui, une fois (WSL est déjà présent) | non, sauf l'installeur Visual Studio |
| **Couverture** | ~85 % : toute la structure et la thermique, Elmer, SU2, fusée, drone (ArduPilot SITL), MuJoCo, mécatronique, fabrication | + OpenFOAM complet, PX4, crocoddyl/TSID, preCICE, apprentissage sur carte graphique | + MoveIt, ros2_control, Gazebo, chaîne ROS de bout en bout |
| **Taille** | 6 à 10 Go selon les profils | + ~20 Go | + ~13 Go |
| **Pour qui** | les trois projets jusqu'aux tests virtuels | CFD lourde, PX4, recherche en locomotion | humanoïde piloté par ROS 2 |

A est le socle des deux autres : B et C s'y ajoutent, ils ne le remplacent pas.

---

## 11. Phases : ordre de construction

Chaque phase se termine quand ses cas de référence passent, que la suite complète reste verte, et
qu'un exemple réel a fait tout le tour de la boucle.

| Phase | Contenu | Taille |
|---|---|---|
| **0. Fondations** | FreeCAD 1.1.4 et nouvelle passe de tests. pixi + `pixi.lock` + `install.ps1` (profil `base`). Dossier `adapters/` avec le lanceur (sous-processus, délai, JSON, cache). Outils `simulate`, `job`, `tools_status`. `study` minimal (hypothèses + journal). Correction des bugs connus utiles aux assemblages : repère des opérations Part dans un conteneur déplacé, répétition PartDesign hors pièce. | L |
| **1. Structure et thermique** | CalculiX : pression, gravité, rotation, modal, flambage, thermique, dilatation, plasticité, contact ; pyLife (fatigue) ; GIF d'un mécanisme. Cas NAFEMS. | M |
| **2. Fusée** | RocketPy, OpenRocket (orlab), ThrustCurve, CEA, tuyère, SU2, refroidissement 1D. Test : vol Monte-Carlo. | M |
| **3. Drone** | AeroSandbox, XFOIL, AVL, QPROP, JSBSim, ArduPilot SITL, python-control ; Elmer et CfdOF. Test : mission SITL. | M |
| **4. Humanoïde** | URDF → MJCF, MuJoCo, Pinocchio/mink, module d'équilibre, dimensionnement des actionneurs, vidéos. Test : debout, poussée, pas. | L |
| **5. Mécatronique et fabrication** | ngspice, KiCad, PlatformIO/Renode ; projet PrusaSlicer, vérification CAMotics, SheetMetal et imbrication. | M |
| **6. Optimisation** | `study` complet : Optuna/pymoo, SALib, beso ; dossier de justification. | M |
| **7. Plug and play** | Plugin et marketplace Claude Code, tous les profils, documentation, CI. | S |

Les phases 2, 3 et 4 se font dans l'ordre du premier projet choisi.

---

## 12. Décisions à prendre (propriétaire)

1. **Le premier projet :** fusée, drone ou humanoïde. Il fixe l'ordre des phases 2 à 4.
2. **Le scénario :** A seul, A + B (WSL2) ou A + C (ROS 2 avec Visual Studio Build Tools).
3. **FreeCAD 1.1.4 tout de suite** (recommandé, sécurité), avec toute la suite repassée dessus.
4. **Estimation d'impression :** autoriser l'IA à lancer PrusaSlicer pour obtenir le temps et la
   matière ? Cela génère un G-code temporaire, supprimé, jamais envoyé. Par défaut : **non**.
5. **Outils AGPL** (OpenModelica, RCAIDE) : acceptés comme programmes séparés, ou écartés ?
6. **Moteur-fusée :** confirmer le périmètre (thermochimie, tuyère, refroidissement, structure d'un
   moteur liquide ou hybride ; propergol solide exclu).

---

## 13. Outils écartés et pourquoi

| Outil | Raison |
|---|---|
| Isaac Sim / Isaac Lab | Cœur (Kit SDK) et ressources propriétaires, installation énorme |
| FluidX3D | Licence non libre (interdit l'usage commercial, militaire et l'entraînement d'IA) |
| RASAero II, RPA | Fermés (utilisables à la main pour recouper, jamais dans la boucle) |
| Docker Desktop | Non libre, demande les droits administrateur |
| AirSim, Colosseum, Project AirSim | Gelé, archivé, ou dépendant d'Unreal (licence non libre) |
| gym-pybullet-drones, PyBullet, Brax, Webots | Pas de version Windows propre, ou projet à l'arrêt |
| humanoid-gym, legged_gym | Isaac Gym abandonné ; humanoid-gym sans licence |
| RobotCAD | Exige Python 3.12 dans FreeCAD (1.1 a 3.11) ; conflit d'espace de noms avec CROSS |
| SuperSlicer, ToOptix, SUAVE, FAST-UAV | Arrêtés ou dépassés |
| Dépôts de tuyère ou refroidissement sans licence (evand/nozzle, lrekit, AeroMOC, THERMAT) | Sans licence = aucun droit d'utilisation |
| Serveurs MCP tiers (imprimantes, MAVLink, rosbridge, ParaView, Blender…) | Exécutent du code ou commandent du matériel sans authentification ; on reprend les idées, pas le code |
| `freecad` de conda-forge | Instantanés hebdomadaires, pas une version stable |
| Mettre à jour l'atelier Rocket vers 5.1.3 | Exige numpy ≥ 2.4.1, absent du Python de FreeCAD 1.1 |

---

## Annexe : versions relevées le 2026-11-14

| Outil | Version | Licence | Installation |
|---|---|---|---|
| FreeCAD | 1.1.4 (2026-09-28) ; 26.3 en version candidate | LGPL-2.1+ | archive 7z officielle + SHA256 |
| CalculiX | 2.23 | GPL-2.0+ | fourni avec FreeCAD / conda-forge |
| Gmsh / Netgen | 4.15.2 / 6.2.2608 | GPL-2.0+ / LGPL-2.1 | fourni / pip |
| Elmer | 26.2 | GPL-2.0+ (bibliothèques LGPL) | zip officiel sans interface, 130 Mo |
| SU2 | 8.6.0 (2026-10-30) | LGPL-2.1 | zip Windows, ~30 Mo |
| CfdOF + blueCFD-Core | 1.37.4 / 2024-1 (OpenFOAM 12) | LGPL-3.0 / GPL-3.0 | gestionnaire d'addons FreeCAD |
| PyVista / VTK / ParaView | 0.49.2 / 9.7.2 / 6.2.1 | MIT / BSD-3 / BSD-3 | pixi |
| pyLife | 2.3.1 | Apache-2.0 | pip |
| Optuna / pymoo / SALib | 5.0.0 / 0.6.2 / 1.6.0 | MIT / Apache-2.0 / MIT | pip |
| beso | — | LGPL-3.0 | git figé |
| RocketPy | 1.13.0 | MIT | pip |
| OpenRocket + orlab | 24.12 + 0.10.0 | GPL-3.0+ / GPL-2.0 | jar (SHA256) + pip, Java 17 |
| NASA CEA | 3.3.4 | Apache-2.0 | pip (`cea`) |
| CoolProp / Cantera | 8.0.0 / 3.2.0 | MIT / BSD-3 | pip |
| pygasflow | 1.4.1 | GPL-3.0 | pip |
| AeroSandbox / NeuralFoil | 4.2.10 / 0.3.3 | MIT | pip |
| XFOIL / AVL / QPROP | 6.99 / 3.52 / 1.22 | GPL | exécutables officiels |
| JSBSim | 1.3.3 | LGPL-2.1+ | pip |
| ArduPilot SITL | Copter 4.7.0 | GPL-3.0 | binaires Windows officiels |
| pymavlink / MAVSDK-Python | 2.4.50 / 4.2.0 | LGPL-3.0 / BSD-3 | pip |
| python-control / CasADi | 0.10.2 / 3.8.2 | BSD-3 / LGPL-3.0+ | pip |
| MuJoCo (+ Menagerie) | 4.0.1 (2026-11-11) | Apache-2.0 | pip |
| Pinocchio / Pink / mink | 4.1.0 / 4.4.0 / 1.4.0 | BSD / Apache-2.0 / Apache-2.0 | conda-forge / pip |
| SymPy / yourdfpy | 1.14.0 / 0.0.60 | BSD-3 / MIT | pip |
| ROS 2 Lyrical (RoboStack) | LTS jusqu'en 2031 | Apache-2.0 | pixi (option) |
| KiCad | 10.0.8 | GPL-3.0+ | installeur officiel |
| ngspice | 47 | BSD-3 (principalement) | archive officielle |
| PlatformIO / Renode | 6.2.0 / 1.17.0 | Apache-2.0 / MIT | pip / zip |
| OpenModelica (option) | 1.27.2 | OSMC-PL (AGPL hors membres) | installeur |
| PrusaSlicer | 2.9.7 | AGPL-3.0 | zip portable |
| CAMotics | 1.2.0 | GPL-2.0+ | installeur (lecture de G-code seulement) |
| sparrow | — | MIT | à compiler (Rust) |
| SheetMetal (FreeCAD) | 0.8.27 | LGPL-2.1+ | gestionnaire d'addons |
| pixi | 0.84.2 | BSD-3 | binaire figé + SHA256 |
