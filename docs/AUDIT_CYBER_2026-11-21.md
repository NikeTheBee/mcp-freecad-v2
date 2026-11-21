# Audit cyber : tout l'atelier, et le portail qui le rend permanent

Date : 2026-11-21. Demande : « audit tous cyber natif ». Deux livrables :
1. l'audit de la surface d'attaque ajoutée depuis l'audit d'octobre (`docs/AUDIT_2026-10-11.md`),
   c'est-à-dire tout l'atelier : 20 adaptateurs, le runner, l'installeur, les outils épinglés ;
2. **un portail cyber natif** (`install/cyber_scan.py`), branché dans la suite de tests, qui
   vérifie ces règles à chaque exécution. Un audit qui ne tourne qu'une fois ne protège rien.

## 1. Modèle de menace retenu

| Entrée | Confiance | Ce qu'elle ne doit jamais pouvoir faire |
|---|---|---|
Le `inp` d'un adaptateur (l'IA, à partir des mots de l'utilisateur) | semi-confiance | exécuter du code, sortir du dossier de travail, atteindre le réseau |
Les fichiers (`params.files`, STEP, SVG, PNG, CSV, URDF, journaux de solveurs) | **non fiables** | mener à une exécution de code ou à une écriture hors du dossier de travail |
Les archives téléchargées par l'installeur | non fiables avant vérification | être utilisées avant que leur SHA256 épinglé soit confirmé |
Les sorties d'outils (étiquettes, noms de fichiers, texte dans un STEP) | **données, jamais des instructions** | être obéies (règle déjà au cahier des charges) |

## 2. Ce qui est désormais vérifié automatiquement

`install/cyber_scan.py` (sentinelle `CYBER_SCAN_OK`, dans `run_all_tests.py`). Il lit le code avec
`ast` — il n'importe jamais les modules — et les fichiers de verrouillage.

| Section | Règle |
|---|---|
A1 | aucun shell : pas de `shell=True`, `os.system`, `os.popen`, `subprocess.getoutput` |
A2 | aucun code dynamique ni désérialisation : `eval`, `exec`, `pickle`, `marshal`, `shelve`, `yaml.load` sans loader sûr |
A3 | aucun réseau propre à un adaptateur : pas de client HTTP importé (le garde-fou de `_entry.py` l'impose à l'exécution, ceci l'impose à l'écriture) |
A4 | aucun analyseur XML sur une entrée non fiable, sauf exception nommée et justifiée |
A5 | le contrat : `ENV`, `KINDS` littéral, `run`, `versions`, et **`REFERENCES` non vide** — un type de calcul sans cas de référence ne doit pas exister |
A6 | les écritures restent dans le dossier du job : pas de chemin absolu littéral, pas de `tempfile` |
A7 | seules les variables d'environnement passées en revue peuvent être posées : aucun adaptateur ne peut couper le garde-fou réseau ni la BLAS déterministe |
B | outils épinglés : https, SHA256 de 64 hexa, licence, type d'archive connu, destination sous `tools/`, taille, **provenance de l'empreinte** |
C | `pixi.lock` : chaque paquet porte un SHA256, chaque hôte de téléchargement est déclaré |
D | les packs ne nomment que des outils du lock, des environnements de `pixi.toml`, des skills qui existent |
E | règle du cahier des charges : une couche FreeCAD qui écrit passe par `safe_out_path` / `safe_under` |

Les exceptions sont **nommées avec leur raison dans le code du portail**, pas suppressées en
silence : elles restent visibles en revue et un nouvel adaptateur ne les hérite pas.

État au 2026-11-21 : **20 adaptateurs, outils épinglés, `pixi.lock`, packs et couches — aucune
violation.** Plus `security_scan.py` : 388 fichiers suivis, aucun secret, aucun fichier sensible,
écoute locale seulement. L'adresse de courriel privée du propriétaire n'apparaît dans **aucun**
fichier suivi (elle n'est que dans la paternité des commits, à sa place).

## 3. Trouvailles

**F1 — `firmware.py` écrivait dans le temp du système (FAIBLE, hygiène — CORRIGÉ).**
`tempfile.mkdtemp(prefix="mcpfc_renode_")` au lieu du dossier du job. À la relecture, le code
nettoyait déjà son dossier (`rmtree` en `finally`), donc ce n'était pas une fuite ; mais le temp
du système n'est pas le bon endroit, et le projet a déjà connu une accumulation de 3,8 Go de
dossiers temporaires. **Corrigé** : le dossier de travail de Renode est maintenant `work/renode`,
dans le dossier que le runner crée et supprime pour chaque exécution ; le contrôle « pas d'espace
dans le chemin », qu'exige Renode, est conservé. Les trois cas de référence de `firmware`
repassent (Renode tourne vraiment : 18,8 s sur le premier). **Et l'exception correspondante a été
retirée du portail** : la règle A6 interdit désormais `tempfile` à *tous* les adaptateurs, et
l'arbre passe quand même.

*Observation en passant* : Renode crée **son propre** dossier temporaire (`renode-<pid>`). Le
runner redirige `TEMP` et `TMP` vers `MCPFC_HOME`, donc il reste à l'intérieur de l'atelier. Rien
à corriger de notre côté, mais c'est le genre de chose qu'il faut avoir regardée pour le dire.

**F2 — URDF/SDF analysés avec l'ElementTree de la bibliothèque standard (FAIBLE, informatif).**
`dynamics.py` et `gazebo.py`. Pas de XXE : l'ElementTree standard ne résout pas les entités
externes, donc ni lecture de fichier ni SSRF. Le risque résiduel est l'expansion d'entités, soit
un analyse lente sur le fichier de l'utilisateur lui-même. *Correctif proposé* : `defusedxml` si
un jour il entre dans ces environnements. Exception inscrite au portail avec cette raison.

**F3 — provenance d'empreinte absente pour `pixi` et `freecad` (FAIBLE, corrigé).**
Les deux étaient épinglés par SHA256, mais sans `sha_source` : l'empreinte était vérifiée, son
origine n'était pas documentée. **Corrigé** : recalculée depuis l'archive du cache de l'atelier,
celle qui a installé la copie en service — les deux correspondent — et la provenance est inscrite.

**Note de méthode.** Première lecture de `pixi.lock`, j'ai cru que neuf nouveaux paquets
n'avaient pas d'empreinte : je lisais la liste de références des environnements, pas la section
`packages:`. Vérification refaite : **tous** portent leur SHA256. C'est le genre de faux positif
qu'un audit doit attraper sur lui-même avant de le publier.

## 4. Vérifié et sain, avec la preuve

- **Injection de code par expression (`mdo.py`)** — c'est la surface la plus sensible ajoutée :
  des chaînes de l'appelant atteignent `ExecComp` d'OpenMDAO, qui les compile. 13 tentatives de
  contournement passées à `_check_expr`, **13 refusées** : `__import__("os").system(...)`,
  `x.__class__.__mro__[1].__subclasses__()`, `getattr(x,"real")`, `x.real`, `x[0]`, `open(...)`,
  `eval("1")`, `(lambda: 1)()`, `[i for i in range(3)]`, `x; import os`, `x if x else y`,
  `1if x else 2`, `"a"+"b"`. Les deux expressions légitimes (`exp(x) + sqrt(y)`,
  `2*x - y/3 + pi`) passent. Le tokeniseur doit consommer **toute** la chaîne : l'accès par
  point, l'indexation, les littéraux de chaîne et tout nom non déclaré sont refusés par
  construction, et un nom de variable doit valoir `[A-Za-z_]\w{0,30}` sans masquer une fonction.
- **Injection dans un script généré (`vsp.py`)** — l'adaptateur écrit un fichier AngelScript
  exécuté par `vspscript.exe`. Toutes les valeurs interpolées passent par `_f()` / `_pos()`, donc
  sont des flottants formatés en `%.9g`. La seule interpolation non numérique est un nom de
  fichier `.vsp3` trouvé dans le dossier du job ; Windows interdit déjà `"` dans un nom de
  fichier, et **une garde a été ajoutée** (lettres, chiffres, espace, point, tiret, souligné) :
  deux serrures sur la même porte.
- **Pas de XXE dans les cinq nouveaux adaptateurs** : le SVG est lu à l'expression régulière, le
  CSV par le module `csv`, le STL octet par octet. Aucun analyseur XML, donc rien à exploiter.
- **Pas de `pickle`, `marshal`, `yaml.load`, `eval` ni `exec`** dans le code ajouté.
- **Chemins** : les adaptateurs n'écrivent que dans le dossier fourni par le runner, avec des
  noms de base en dur ; les deux seuls noms venant de l'entrée (`draw.image`, `draw.svg`) sont
  filtrés par expression régulière et résolus sous ce dossier.
- **Chaîne d'approvisionnement de la vague 2** : dix nouveaux paquets, **tous** de conda-forge
  avec SHA256, **aucun** nouveau paquet PyPI ; deux nouveaux outils épinglés en https avec
  empreinte, licence et provenance (OpenVSP sous NASA Open Source Agreement 1.3, innoextract sous
  zlib).
- **L'installeur blueCFD-Core de 1,2 Go** : son SHA1 est **identique** au SHA1 publié par le
  projet, donc l'archive est authentique ; il sera **ouvert, jamais exécuté** (innoextract), et
  les membres extraits sont vérifiés contre la traversée de chemin avant d'être déplacés.
- **Frontière MCP inchangée** : les vagues 1 et 2 n'ont ajouté aucun outil MCP ni aucune couche
  FreeCAD. Les nouveaux types de calcul passent par `simulate`, qui valide le `kind` contre le
  registre et délègue au runner déjà audité.
- **CI** : le travail de secret (gitleaks sur tout l'historique) et les quatre tâches sont vertes
  sur le dernier commit poussé.

## 5. Ce qui reste

- Appliquer F1 (dossier du job pour Renode) avec son test.
- `defusedxml` dans `robot` et `ros` si l'occasion se présente (F2).
- ~~Un SBOM (CycloneDX)~~ **fait** : `install/sbom.py` le produit hors ligne depuis les deux
  fichiers de verrouillage, sans aucun appel réseau et sans rien importer. 1321 composants, dont
  **1320 avec leur SHA-256** (le seul sans empreinte est la base vendorisée, qui n'est pas un
  téléchargement) et 25 avec une licence enregistrée. Il ne devine rien : un paquet conda ne
  porte pas sa licence dans le lock, donc ces champs restent vides et c'est dit. Le fichier
  produit n'est pas versionné (il est reproductible depuis les locks, qui le sont).
- Une tâche de CI qui lance `cyber_scan.py` seul sur une machine propre (il tourne déjà dans la
  suite, donc dans les quatre tâches existantes).
- Le portail ne lit pas le sous-arbre vendorisé `server/freecad-mcp/` : il a sa propre
  `SECURITY.md` et ses propres tests, et l'audit d'octobre le couvre.
