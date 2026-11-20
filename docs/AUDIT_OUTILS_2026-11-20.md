# Audit outils : CFD haut de gamme, aéroélasticité (flutter), POGO, dessin

Date : 2026-11-20. Demande : « ajouter une solution de dessin (type Paint), ANSYS Fluent pour les
fluides, flutter analysis, POGO analysis... lister toutes les fonctionnalités, regarder ce qu'on a,
ce qui manque, et trouver des équivalents open source, libres et complets ».

Méthode : pour chaque outil payant, on liste ses fonctions **par famille**, on marque celles qui
nous servent vraiment (sous-marin, ROV, drone, fusée, satellite, structure, électronique), puis on
confronte à l'atelier existant (`server/adapters/*.py`, `pixi.toml`, `install/tools.lock.json`).
Trois issues possibles par trou : **(A)** un outil libre équivalent existe et s'installe sur
Windows sans droits admin, on l'ajoute au lock ; **(B)** la brique existe en bibliothèque libre
mais la méthode d'ingénierie est à écrire, on écrit l'adaptateur et ses cas de référence ;
**(C)** rien de libre n'existe, on l'implémente depuis la littérature publique, avec des cas
publiés comme preuve.

Règle non négociable du projet : aucun résultat n'est servi sans cas de référence vérifié
(`REFERENCES` dans l'adaptateur). Un solveur sans référence est marqué `UNKNOWN`, jamais `OK`.

---

## 1. ANSYS Fluent : fonctionnalités et couverture

Fluent est un solveur volumes finis généraliste. Liste par famille, avec la pertinence pour nous
(5 étoiles = besoin immédiat, 1 étoile = anecdotique pour nos objets).

| # | Fonction Fluent | Pertinence | Ce qu'on a aujourd'hui | Verdict |
|---|---|---|---|---|
| 1 | Incompressible RANS (k-eps, k-omega SST, Spalart-Allmaras, transition) | ***** | SU2 8.6 (SST + SA, incompressible et compressible), Elmer (k-eps) | **couvert** |
| 2 | Compressible, transsonique, supersonique, chocs | ***** | SU2 (Euler + RANS, schémas JST/Roe, 2D et 3D) | **couvert** |
| 3 | Maillage (surface wrap, polyédrique, couches limites) | **** | Gmsh 4.15 (tétra + couches) ; pas de hexa automatique ni de wrapping | **partiel** |
| 4 | Conjugate heat transfer (fluide + solide) | **** | SU2 CHT, Elmer, CalculiX (thermique seule) | **couvert** |
| 5 | Multiphase VOF, surface libre | ***** | **rien** | **trou n°1** |
| 6 | Cavitation (Schnerr-Sauer, Zwart) | ***** (hélice de sous-marin, pompe de fusée) | rien, sauf un indice de cavitation analytique | **trou n°2** |
| 7 | Euler-Euler granulaire, DDPM, film mince, ébullition | ** | rien | reporté |
| 8 | Lagrangien de particules (DPM, spray, injection, givrage) | *** | rien | reporté |
| 9 | LES, DES, SAS (turbulence résolue) | *** | SU2 (DES hybride), coût très élevé | **partiel** |
| 10 | Combustion CFD (flammelette, EDC, PDF, suie, rayonnement) | **** (chambre de fusée) | Cantera 3.2 : équilibre et cinétique 0-D, pas de CFD réactive 3-D | **partiel assumé** |
| 11 | Rayonnement (DO, S2S, P1) | ** | Elmer (diffusion + rayonnement simple), `sat_thermal` analytique | **partiel** |
| 12 | Espèces, mélange, transport scalaire | *** | SU2 species, Elmer, NGSolve (PDE sur mesure) | **couvert** |
| 13 | Maillage mobile : MRF, sliding mesh (hélice en rotation) | ***** | **rien** | **trou n°3** |
| 14 | Dynamic mesh, remaillage, overset | *** | SU2 déformation de maillage (FFD, mouvement imposé) | **partiel** |
| 15 | 6-DOF : corps libre piloté par l'écoulement | ***** (ROV lâché, séparation d'étages, flottaison) | corps rigide hors CFD (Gazebo, RocketPy), aucun couplage | **trou n°4** |
| 16 | FSI (couplage structure, System Coupling) | ***** | SU2 a un module élasticité interne ; aucun couplage SU2 vers CalculiX | **trou n°5** |
| 17 | Aéroacoustique (Ffowcs Williams-Hawkings, large bande) | *** (bruit de drone, signature sonar) | **rien** | **trou n°6** |
| 18 | Solveur adjoint et optimisation de forme | **** | SU2 adjoint discret + `cfd_su2_shape`, déjà exposé | **couvert** |
| 19 | UDF, personnalisation | **** | adaptateurs Python + NGSolve (PDE arbitraire) | **couvert, et en mieux** |
| 20 | Turbomachine, couplage système 1-D, électrochimie, CFD-DEM | * à ** | rien | hors périmètre |
| 21 | Post-traitement (CFD-Post) | *** | ParaView 6 (env `post`), PyVista | **couvert** |

**Lecture** : on couvre déjà l'aérodynamique externe, le compressible, le CHT, l'adjoint et le
post-traitement. Ce qui manque, c'est **tout ce qui touche l'eau avec une interface libre** (VOF,
cavitation, sloshing), **l'hélice en rotation** et **les couplages** (6-DOF, FSI, acoustique).
C'est exactement le domaine sous-marin, ROV et propulsion : le trou est réel et prioritaire.

### Réponse open source retenue

| Besoin | Outil libre | Licence | Windows | Pourquoi lui |
|---|---|---|---|---|
| VOF, surface libre, cavitation, MRF et sliding mesh, 6-DOF flottant, FW-H, LES | **OpenFOAM 12** via **blueCFD-Core 2024-1** | GPL-3 | binaire natif Windows (MSYS2 et ParaView 5.11.2 embarqués ; chemin d'installation sans espace) | le seul libre qui couvre d'un coup les six trous ci-dessus : `interFoam` (VOF), `interPhaseChangeFoam` (cavitation), `MRFSimpleFoam`, `overInterDyMFoam` (6-DOF), `libacoustics` (FW-H) |
| Sloshing, impact de vague, éclaboussure, remplissage | **DualSPHysics 5** | LGPL selon le dépôt et l'article, GPL-3 selon SourceForge : on lira le fichier LICENSE avant de l'ajouter | binaires Windows CPU et CUDA | SPH sans maillage, là où le VOF devient cher ou instable |
| Multi-corps, câbles, granulaire, roues et chenilles | **pychrono 10** | BSD-3 | conda-forge win-64, **vérifié** | complète MuJoCo pour les contacts et le terramécanique ; FEA de poutres et de câbles incluse |
| CFD incompressible industriel, alternative | code_saturne | GPL-2 | surtout Linux | **non retenu** : redondant avec OpenFOAM, pas de binaire Windows propre |

Choix assumé : **pas de WSL2 obligatoire**. blueCFD-Core est natif, donc l'installation reste
« une commande, sans droits admin ». WSL2 reste une option documentée pour qui veut l'OpenFOAM
officiel et ses paquets Linux plus à jour.

---

## 2. Flutter analysis : fonctionnalités et couverture

La référence payante n'est pas Fluent mais MSC/NX Nastran SOL 145 (flottement), SOL 146 (réponse
dynamique aéroélastique), ZAERO. C'est une autre famille d'outils.

| # | Fonction | Pertinence | Ce qu'on a | Verdict |
|---|---|---|---|---|
| 1 | Matrices aérodynamiques instationnaires (AIC) par **Doublet Lattice Method**, subsonique | ***** | rien | **trou** : PanelAero |
| 2 | Vortex Lattice stationnaire (dérivées, portance) | ***** | AeroSandbox VLM (`wing`) | **couvert** |
| 3 | Base modale structurale (masse et raideur) | ***** | CalculiX `fem_modal`, déjà validé | **couvert** |
| 4 | Couplage spline structure vers grille aérodynamique | ***** | rien | **trou** (à écrire, surfaces de plaque mince ou RBF) |
| 5 | Solution de flottement **méthode p-k** (V-g et V-f, vitesse et fréquence critiques) | ***** | rien | **trou** : à écrire |
| 6 | Méthodes k (américaine) et p (racines complexes) | *** | rien | p-k suffit, k en option |
| 7 | **Divergence** statique aéroélastique | ***** | rien | **trou** : sort du même système |
| 8 | Efficacité de gouverne, inversion d'aileron | **** | rien | **trou** : même machinerie |
| 9 | Rafale discrète 1-cos et turbulence continue (von Karman, PSD) | ***** (charges de drone et de lanceur) | rien | **trou** : PanelAero |
| 10 | Flottement **transsonique** par CFD instationnaire | *** | SU2 : harmonic balance et pitch-plunge existent dans le code, non exposés chez nous | **partiel** |
| 11 | Flottement non linéaire, grandes déformations (aile souple, drone HALE) | **** | rien | **trou** : SHARPy |
| 12 | **Whirl flutter** (hélice et nacelle souple), rotors | **** (drone, eVTOL) | rien | **trou** : MBDyn ou DUST, vague 3 |
| 13 | **Fin flutter** d'ailerons de fusée | ***** | rien | **trou** : à écrire (c'est même un manque connu d'OpenRocket) |
| 14 | Flottement de panneau (revêtement, coque) | *** | rien | reporté |
| 15 | Amortissement structural, viscoélasticité | *** | partiel (modal) | partiel |

### Réponse open source retenue

| Brique | Outil | Licence | Disponibilité | Rôle |
|---|---|---|---|---|
| AIC subsonique DLM et VLM | **PanelAero** (DLR, Institut für Aeroelastik) | BSD-3 | **conda-forge win-64 vérifié** (`panelaero 2025.08`) | AIC validées face à MSC.Nastran pour les schémas d'intégration parabolique et quartique, sur des configurations aile, empennage et dérive, à plusieurs Mach et fréquences réduites |
| Flottement non linéaire, aile très souple, UVLM | **SHARPy** (Imperial College) | **BSD-3 vérifié dans le dépôt** | pip, dépendances C++ à compiler | vague 3, pour les ailes à grand allongement |
| Référence de méthode p-k | littérature : Hassig 1971 ; aile de **Goland**, **AGARD 445.6**, section typique de Theodorsen | — | — | cas de validation |
| DLMpro et FlutterWing | GPL-3, MATLAB | non | — | **non retenu** : exige MATLAB |

Conclusion : cas **(B)**. Les briques existent, la méthode d'ingénierie est à écrire. On écrit
l'adaptateur `aeroelastic` (p-k, divergence, efficacité de gouverne, rafale, fin flutter), avec
PanelAero pour l'aérodynamique instationnaire et CalculiX pour les modes, et on le prouve sur des
cas publiés.

---

## 3. POGO analysis : fonctionnalités et couverture

POGO : instabilité longitudinale d'un lanceur à ergols liquides. La structure vibre, les lignes
d'alimentation et la pompe modulent la poussée, la poussée réexcite la structure. Boucle fermée.

| # | Fonction | Pertinence | Ce qu'on a | Verdict |
|---|---|---|---|---|
| 1 | Modèle hydraulique des lignes (inertance, compliance, résistance, matrices de transfert) | ***** | rien (`fluids` disponible pour les pertes de charge) | **trou** |
| 2 | Fonction de transfert de pompe (gain de cavitation, compliance de cavitation, temps de transit) | ***** | rien | **trou** |
| 3 | Dynamique de réservoir et de colonne de liquide, variation en vol | ***** | rien | **trou** |
| 4 | Base modale longitudinale de la structure | ***** | CalculiX `fem_modal` | **couvert** |
| 5 | Gain chambre vers poussée, retard de combustion | **** | Cantera, `nozzle` | **partiel** |
| 6 | Bouclage, valeurs propres, **marges de gain et de phase** (Nyquist), amortissement par mode | ***** | `control` 0.10 (python-control), déjà dans `socle` | **brique disponible** |
| 7 | Dimensionnement d'**accumulateur** (correcteur POGO) | ***** | rien | **trou** |
| 8 | Balayage du temps de vol (stabilité le long de la trajectoire) | **** | rien | **trou** |
| 9 | Couplage de combustion haute fréquence (chug, buzz, screech) | *** | rien | reporté : famille voisine, pas POGO |

### Réponse open source : il n'y en a pas

- **ML_POGO (NASA MFS-33024-1)** modélise exactement cela, mais le catalogue NASA le marque
  « U.S. Release Only » : non libre, donc inutilisable ici.
- Les codes académiques (lignes résolues par la méthode des caractéristiques) ne sont pas publiés.
- Ce qu'on trouve sur GitHub sous ce nom est un script d'étudiant sans validation.

Cas **(C)** : on l'implémente. La méthode est publique et stable depuis les années 1970 (lignes en
matrices de transfert, modèle de pompe, bouclage sur base modale ; guides NASA de prévention du
couplage structure-propulsion, littérature Titan II et Saturn V). Validation sur un cas analytique
à 2 degrés de liberté dont la racine se calcule à la main, plus un cas publié. Chaque résultat
affiche ses limites : linéaire, petites perturbations, autour d'un point de vol figé.

---

## 4. Dessin : « Paint intégré »

Le besoin réel derrière la demande : **griffonner une forme et qu'elle devienne de la CAO**, et
annoter ou illustrer sans sortir de la boucle.

| # | Fonction d'un outil de dessin | Pertinence | Ce qu'on a | Verdict |
|---|---|---|---|---|
| 1 | Dessin à main levée (souris, tablette) | ***** | rien | **trou n°1** |
| 2 | Croquis vers contour fermé vers **esquisse FreeCAD paramétrique** | ***** | import SVG de FreeCAD présent mais non exposé | **trou n°2** |
| 3 | Photo, scan ou capture Paint vers vectorisation (seuillage, contours, simplification, arcs) | ***** | OpenCV déjà présent (env `robot`) | **brique disponible** |
| 4 | Mise à l'échelle par deux points de référence et une cote connue | ***** | rien | **trou** |
| 5 | Dessin technique coté, cartouche, DXF | ***** | TechDraw, `drawing_sheet`, export DXF | **couvert** |
| 6 | Schémas, diagrammes, graphes | **** | matplotlib, graphviz, ParaView, rendu de vues | **couvert** |
| 7 | Édition vectorielle complète (courbes, booléens, texte en chemins) | *** | rien | Inkscape portable |
| 8 | Peinture, retouche, texture | ** | rien | Krita ou GIMP portables, hors boucle |

### Réponse retenue

1. **Planche à dessin locale** (HTML et canvas, zéro installation) : le tracé à la souris sort en
   SVG, qui entre dans FreeCAD comme esquisse. Marche sur n'importe quel PC, sans téléchargement.
2. **Vectorisation d'image** par OpenCV, avec mise à l'échelle par cote de référence : on accepte
   un PNG de Paint, une photo de croquis papier, un scan de plan.
3. **Inkscape portable** (archive, sans admin) seulement si on veut du vrai dessin vectoriel. La
   version `inkscape` de conda-forge est la 0.91 de 2015, donc **écartée**.
4. **ImageMagick** (conda-forge, vérifié) pour les conversions et les planches d'images.

**Refusé** : piloter le vrai Paint de Windows par automatisation d'interface. Ce n'est ni
reproductible, ni testable, ni scriptable, et ça casse la règle « tout passe par des fichiers ».

---

## 5. Trous relevés en plus (ajouts proposés, non demandés explicitement)

Revue des domaines visés par le projet : aéronautique, spatial, drone, sous-marin, fusée.

| Trou | Pourquoi ça compte | Réponse libre | Statut |
|---|---|---|---|
| **Flambage de coque raidie** (coque de sous-marin, réservoir de lanceur) | dimensionnement n°1 d'un corps sous pression externe | méthodes NASA SP-8007 et ECSS + CalculiX `fem_buckling` : on a le solveur, pas la méthode de coque raidie | à écrire, vague 3 |
| **Stratifiés composites** (CLT, Tsai-Wu, Hashin) | carbone et verre pour drone, aileron, coque | théorie classique des stratifiés à écrire (pas de paquet conda fiable) | à écrire, vague 3 |
| **Propagation de fissure** (da/dN, Paris, Forman, facteurs d'intensité) | tolérance aux dommages d'un corps sous pression cyclé | `pylife` (présent) et solutions de SIF publiées | à écrire, vague 3 |
| **Hélice marine** (BEMT, cavitation, KT-KQ) | ROV et sous-marin : on a la poussée empirique, pas le dessin de pale | QPROP (présent, côté air), OpenProp (MATLAB/Octave) ou un BEMT propre | à écrire, vague 3 |
| **Acoustique sous-marine** (propagation, sonar, détectabilité) | `arlpy` est **déjà** dans l'environnement marin, mais **le binaire BELLHOP manque** | BELLHOP (Acoustics Toolbox, libre) à mettre au lock | **ajout immédiat du binaire** |
| **Antennes et CEM** (diagramme, gain, adaptation) | liaison télémétrie drone et ROV, traversée de l'eau | openEMS (FDTD, GPL) ou NEC2 ; `scikit-rf` déjà présent pour les circuits | vague 3 |
| **Géométrie conceptuelle d'aéronef et VSPAERO** | passer d'une idée à des dérivées de stabilité en minutes | **OpenVSP** (NASA) ; conda-forge n'a que `openvsp-code-eli`, donc binaire officiel au lock | vague 2 |
| **MDO multidisciplinaire** (optimisation couplant plusieurs solveurs) | « on l'optimise en entier » | OpenMDAO 3.45 et pyOptSparse, **vérifiés sur conda-forge win-64** | vague 2 |
| **Couplage FSI par fichiers** | flottement CFD, coque de sous-marin déformable | preCICE n'a pas de paquet win-64, donc couplage par fichiers maison (déjà notre pratique) | vague 3 |
| Thermohydraulique de tuyauterie (`fluids`, `thermo`, `ht`) | lignes POGO, refroidissement régénératif, circuit de ballast | **vérifiés sur conda-forge** | **ajout immédiat, requis par POGO** |

---

## 6. Plan d'exécution

**Vague 1 (ce qui a été demandé, en cours)**

1. `aeroelastic` : AIC DLM (PanelAero), p-k, divergence, efficacité de gouverne, rafale 1-cos,
   **fin flutter** de fusée. Cas de référence : section typique de Theodorsen, aile de Goland,
   AGARD 445.6, formule d'empennage publiée.
2. `pogo` : lignes en matrices de transfert, pompe, réservoir, bouclage modal, marges, taille
   d'accumulateur, balayage de temps de vol. Cas : 2 DDL analytique et cas publié.
3. `draw` : planche à dessin HTML, image vers contours vers SVG vers esquisse FreeCAD, mise à
   l'échelle par cote de référence.
4. Ajouts d'environnement : `panelaero`, `fluids`, `thermo`, `ht`, plus BELLHOP au lock.

**Vague 2** : OpenFOAM via blueCFD-Core (pack `fluides-plus` : VOF, cavitation, MRF, 6-DOF, FW-H),
OpenVSP, OpenMDAO et pyOptSparse, DualSPHysics (après lecture de sa licence).

**Vague 3** : SHARPy, MBDyn ou DUST (whirl flutter), composites, fissuration, coque raidie, hélice
marine, openEMS, couplage FSI par fichiers.

Règle de progression inchangée (cahier des charges §11) : **une capacité à la fois, validée avant
la suivante**.

## 7. Sources

- Capacités Fluent : [page capacités ANSYS](https://www.ansys.com/products/fluids/ansys-fluent/capabilities),
  [tableau de capacités v252](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/flu_ug/flu_gs_sec_gs_capabilities.html),
  [comparatif 2020 R2 (PDF)](https://www.4cadgroup.com/content/files/Capabilities-fluids-ansys-2020-R2.pdf)
- Alternatives libres : [OpenFOAM vs Fluent](https://cfdland.com/openfoam-vs-ansys-fluent/),
  [plateformes CFD libres comparées](https://www.resolvedanalytics.com/theflux/comparing-cfd-software-part-2-open-source-cfd-software-packages),
  [ce que chaque CFD libre sait faire, 2026](https://www.deeb-flow.com/en/blog/free-cfd-software)
- OpenFOAM sous Windows : [blueCFD-Core, téléchargements](https://bluecfd.github.io/Core/Downloads/),
  [annonce 2024-1](https://www.cfd-online.com/Forums/openfoam-news-announcements-other/257464-bluecfd-core-2024-1-has-been-released.html),
  [actualités du projet](https://bluecfd.github.io/Core/News/)
- SPH : [DualSPHysics, dépôt](https://github.com/mfkiwl/DualSPHysics),
  [page SourceForge](https://sourceforge.net/projects/dualsphysics/)
- Aéroélasticité : [PanelAero, DLR](https://github.com/DLR-AE/PanelAero),
  [SHARPy](https://github.com/ImperialCollegeLondon/sharpy),
  [FlutterWing et DLMpro, GPL, MATLAB](https://github.com/JonasEiche/FlutterWing),
  [flottement dans SU2, SciTech 2022](https://spiral.imperial.ac.uk/bitstream/10044/1/94187/2/Simiriotis%20-%20Scitech%202022.pdf)
- POGO : [ML_POGO, catalogue logiciel NASA, US Release Only](https://software.nasa.gov/software/MFS-33024-1),
  [analyse en boucle fermée de l'instabilité POGO, DTIC](https://apps.dtic.mil/sti/citations/ADA153121),
  [revue POGO et instabilités de combustion](https://www.researchgate.net/publication/399434665_Launch_Vehicle_POGO_Combustion_Instability_Thrust_Oscillation_Dynamics_Instabilities_and_Mitigation_Techniques)
- Fusée et ROV libres : [OpenRocket, dépôt](https://github.com/openrocket/openrocket),
  [RocketPy, actes SciPy](https://proceedings.scipy.org/articles/majora-212e5952-020),
  [OpenROV](https://en.wikipedia.org/wiki/OpenROV), [BlueROV2](https://en.wikipedia.org/wiki/BlueROV2)
