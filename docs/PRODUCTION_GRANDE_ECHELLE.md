# Cible « production à grande échelle » (niveau grand industriel : défense, aéro, type Thales)

> Rédigé le 2026-10-11, à la suite de [`AUDIT_2026-10-11.md`](AUDIT_2026-10-11.md).
> Nouvel objectif exprimé : un outil prêt pour la production, utilisable à grande échelle par un grand
> industriel, sans bug.
>
> **Statut : HORS PÉRIMÈTRE (non retenu le 2026-10-11).** Le propriétaire a confirmé un usage
> personnel, open source, non commercial. Ce document reste une référence au cas où l'objectif
> changerait. Avis final applicable : [`AUDIT_2026-10-11.md` §8](AUDIT_2026-10-11.md).

---

## 0. La vérité d'abord

### 0.1 « Sans bug possible » n'existe pas

Aucun logiciel n'est garanti sans bug, même dans l'avionique certifiée DO-178C niveau A. Ce qu'un
grand industriel exige réellement, et ce qu'on peut livrer :

1. **Zéro défaut critique ou majeur connu** au moment de chaque version (*release gate* bloquante).
2. **Comportement sûr en cas de doute** : le système répond « INCONNU / à vérifier par un humain »,
   **jamais** « OK » par défaut. C'est l'inverse exact de l'actuel `verify` (audit §3.4).
3. **Preuve par le processus** : exigences → tests → résultats traçables, vérification indépendante,
   revue, audit de sécurité externe.

### 0.2 Avec cet objectif, le verdict de l'audit change

L'audit concluait « garder et refonder » **pour un outil personnel ou open source**. Pour un usage
industriel à grande échelle, la conclusion devient :

> **Repartir de zéro sur une nouvelle architecture**, en récupérant le savoir (skills, CDC, solveur
> cinématique, formules, scénarios de test), mais **ni le code de plomberie, ni la base
> `blwfish/freecad-mcp`, ni le principe d'exécution de Python libre.**

Raison : cinq blocages **structurels**. Aucun ne se corrige par un patch ; chacun impose une autre
architecture.

| # | Blocage | Pourquoi c'est rédhibitoire chez un grand industriel |
|---|---|---|
| B1 | **Exécution de code arbitraire** (`execute_python`) au cœur du système | Un RSSI refuse un outil où un modèle de langage, ou un fichier piégé, peut exécuter n'importe quel code sur un poste ou un serveur. |
| B2 | **Mono-utilisateur, mono-processus, poste Windows, socket local** | Pas d'isolation entre utilisateurs, pas de montée en charge, pas de haute disponibilité. Un crash bloque tout (audit §2.3). |
| B3 | **Dépendance à un upstream à un seul mainteneur** (~20 000 lignes, 300 commits par trimestre) | Non maîtrisable, non qualifiable, *bus factor* = 1. Un industriel veut posséder ou contrôler le cœur. |
| B4 | **IA dans le cloud** (Claude) | Les données de conception défense ne peuvent pas sortir : souveraineté, classification, contrôle export (ITAR/EAR, biens à double usage). Il faut un **LLM hébergé sur site**, donc des outils conçus pour des modèles plus petits. |
| B5 | **Licence CC BY-NC 4.0** | L'usage commercial est interdit sans autorisation écrite : juridiquement, un industriel **ne peut pas** l'utiliser en l'état. |

### 0.3 Réalité commerciale

Un grand groupe n'adopte pas un outil critique fourni par une personne seule. Il demandera au
minimum : une **structure juridique** avec contrat de support et de maintenance, une politique de
sécurité (idéalement ISO 27001), une assurance responsabilité civile professionnelle, un plan de
continuité, et souvent une qualification de sécurité (en France : démarches ANSSI selon la
sensibilité). Ce document décrit le **produit** ; ces éléments sont des prérequis **d'entreprise**
à prévoir en parallèle.

---

## 1. Exigences cibles

| Domaine | Exigence | Mesure d'acceptation |
|---|---|---|
| **Sécurité** | Aucune exécution de code libre : le LLM produit des **opérations typées**, jamais du code | Revue d'architecture + test : aucune route n'évalue de texte comme du code |
| | Isolation par job : conteneur éphémère, sans réseau, système de fichiers en lecture seule sauf `/work`, limites CPU, RAM et temps, profil seccomp | Test d'évasion : un job ne voit ni le réseau ni les autres jobs |
| | Authentification SSO (OIDC/SAML, Active Directory), autorisations RBAC par projet | Test d'accès croisé refusé |
| | Chiffrement TLS 1.3 en externe, mTLS entre services, secrets dans un coffre (Vault) | Scan de configuration |
| | Journal d'audit inaltérable (qui, quoi, quand, sur quel modèle) | Tentative de modification détectée |
| | Fonctionnement **hors ligne / air-gap** complet | Installation et usage sans aucun accès internet |
| | Chaîne logicielle : SBOM CycloneDX, artefacts signés (cosign), SLSA niveau 3, scan CVE avec délai de correction contractuel | SBOM et signatures publiés à chaque version |
| | Test d'intrusion externe annuel | Rapport sans vulnérabilité critique ouverte |
| **Données** | Hébergement sur site, aucune donnée vers l'extérieur (LLM compris) | Capture réseau : zéro flux sortant |
| | Gestion du niveau de sensibilité par projet, rétention configurable, RGPD pour les journaux nominatifs | Revue DPO |
| **Échelle** | Utilisateurs concurrents et jobs simultanés **paramétrables** (dimensionnement cible à fixer avec le client, ex. 500 utilisateurs actifs, 50 jobs simultanés) | Test de charge au double de la cible |
| | Assemblages de grande taille (ex. 10 000 pièces) | Corpus de test « gros assemblages » |
| | Workers horizontaux : ajouter des nœuds = plus de capacité | Montée en charge linéaire mesurée |
| **Fiabilité** | **Aucune perte de travail** : tout modèle est un journal d'opérations rejouable + instantanés | Chaos test : on tue un worker au hasard, le travail reprend ailleurs |
| | Objectif de disponibilité (ex. 99,5 %), délais d'attente et quotas partout, opérations idempotentes | Supervision et alerting |
| | **Déterminisme** : même entrée + même version = même géométrie (versions de FreeCAD/OCCT figées) | Rejeu bit à bit du corpus doré |
| **Qualité géométrique** | Vérificateur **indépendant** à trois états (OK / ÉCHEC / INCONNU) avant toute réponse | Les 21 scénarios pièges de l'audit + corpus doré |
| | Livrables atelier (STL, STEP, G-code, plans) **bloqués** jusqu'à approbation par un ingénieur habilité | Workflow d'approbation tracé |
| **Intégration** | Échanges PLM (Teamcenter, 3DEXPERIENCE, Windchill) via STEP AP242 avec annotations (PMI) | Aller-retour sans perte sur un jeu de test client |
| | API REST/gRPC documentée (OpenAPI) + passerelle MCP pour les clients IA | Tests de contrat automatisés |
| **Exploitation** | Déploiement Kubernetes/OpenShift (Helm), observabilité OpenTelemetry + Prometheus, sauvegardes et plan de reprise testés | Exercice de restauration réussi |

---

## 2. Architecture cible

```
 Utilisateurs (chat web interne · plugin poste · client MCP)
        │  TLS + SSO
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Passerelle API : authentification, RBAC, quotas, audit        │
 └──────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌──────────────────────────────┐      ┌───────────────────────────┐
 │ Orchestrateur IA              │◄────►│ LLM sur site (ex. vLLM +  │
 │ - ~12 outils typés, schémas   │      │ modèle ouvert), décodage  │
 │   courts (< 3 000 tokens)     │      │ contraint par grammaire   │
 │ - ne produit QUE des opérations│     └───────────────────────────┘
 └──────────────────────────────┘
        │  opérations JSON validées (schémas stricts)
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Service « CAD Ops » : contrats pré/post-conditions,           │
 │ journal d'opérations (event sourcing), file de jobs           │
 └──────────────────────────────────────────────────────────────┘
        │  file (NATS / RabbitMQ)
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Pool de workers FreeCAD headless — 1 job = 1 conteneur        │
 │ éphémère : sans réseau, FS lecture seule, cgroups, seccomp    │
 └──────────────────────────────────────────────────────────────┘
        │                                   │
        ▼                                   ▼
 ┌─────────────────────────┐      ┌──────────────────────────────┐
 │ Vérificateur indépendant │      │ Stockage : PostgreSQL (projets│
 │ OK / ÉCHEC / INCONNU     │      │ journal, audit) + objets S3 / │
 │ jamais « OK » par défaut │      │ MinIO (FCStd, STEP, instantanés│
 └─────────────────────────┘      │ versionnés)                   │
                                   └──────────────────────────────┘
```

### Principes non négociables

1. **Le LLM ne génère jamais de code.** Il choisit des opérations dans un vocabulaire fermé
   (`create_sketch`, `pad`, `pocket`, `hole`, `fillet`, `pattern`, `boolean`, `assemble`, `export`, …),
   chacune décrite par un schéma strict. Le code qui les exécute est écrit, relu et testé par des humains.
2. **Le modèle 3D est un journal d'opérations rejouable.** Cela donne en même temps l'annulation, la
   reprise après crash (on rejoue sur un autre worker), l'audit et la reproductibilité.
3. **Les workers sont jetables et sans état.** Un crash du noyau géométrique ne coûte qu'un rejeu.
4. **Deux clés avant tout « OK »** : l'exécuteur et un vérificateur indépendant (validité, solidité,
   interférences, règles métier). En cas de doute : « INCONNU » et escalade vers un humain.
5. **Rien ne part à l'atelier sans signature humaine** (STL, STEP, G-code, plans).
6. **Tout est épinglé et reproductible** : versions de FreeCAD, d'OCCT, du modèle de langage et des
   dépendances, figées par version, avec SBOM.

### Choix du noyau (à trancher en phase 0 par un banc d'essai)

| Option | Pour | Contre |
|---|---|---|
| **FreeCAD 1.1 headless en conteneur** (recommandé par défaut) | Format `.FCStd`, PartDesign, TechDraw, FEM, ateliers existants ; LGPL compatible avec un usage commercial | Image lourde (~1 Go), mono-thread, quelques parties liées à l'interface graphique (export PDF de TechDraw) |
| **OCCT direct via build123d / CadQuery** (Apache-2.0) | Léger, scriptable, rapide à conteneuriser, déterministe | Pas d'historique paramétrique FreeCAD, plans 2D et FEM à reconstruire |

Critère de décision : temps par opération, mémoire, stabilité sur le corpus doré, couverture des
opérations de la phase 1.

---

## 3. Stratégie « zéro défaut critique »

| Couche | Moyen | Seuil bloquant |
|---|---|---|
| Conception | Contrats pré/post-conditions sur chaque opération (ex. après `pocket` : volume strictement inférieur, 1 seul solide) | Violation de contrat = échec de l'opération, jamais un résultat silencieux |
| Tests unitaires | Couverture **des branches** du cœur | ≥ 90 % |
| Tests de propriétés | Hypothesis sur la géométrie : conservation de volume (`vol(A−B) + vol(A∩B) = vol(A)`), invariance par translation, idempotence | 0 contre-exemple sur 10 000 tirages par propriété |
| Corpus doré | 500+ pièces et assemblages à valeurs connues (volume, masse, inertie, quantités de nomenclature, cotes) + les 21 pièges de l'audit | 100 % conforme |
| Tests de mutation | mutmut sur le cœur : les tests doivent tuer les mutants | Score ≥ 80 % |
| Fuzzing | Schemathesis sur l'API, fuzzing des schémas d'opérations | 0 crash, 0 réponse 5xx non gérée |
| Charge et chaos | k6/Locust au double de la cible ; destruction aléatoire de workers | Objectifs de latence tenus, 0 perte de travail |
| Non-régression noyau | Rejeu complet du corpus à chaque montée de version FreeCAD/OCCT | 0 écart géométrique hors tolérance |
| Analyse statique | ruff, mypy `--strict`, bandit, semgrep, pip-audit/OSV | 0 alerte haute |
| Évaluation de l'IA | Banc de 200+ demandes en langage naturel avec résultat attendu, rejoué à chaque changement de modèle ou de prompt | Taux de réussite ≥ seuil fixé ; 0 « OK » sur une pièce fausse |
| Revue | Double relecture obligatoire sur le cœur ; validation et vérification par une personne qui n'a pas écrit le code | Aucune fusion sans 2 approbations |
| Version | 0 défaut critique ou majeur ouvert, SBOM signé, notes de version | Porte de sortie automatique |

---

## 4. Ce qu'on récupère du projet actuel

| Récupéré | Devient |
|---|---|
| 15 skills et le cahier des charges | Spécification du vocabulaire d'opérations et des règles métier, prompts système |
| `kinematics.py`, formules de `aero.py`, `urdf_aug.py` | Modules de calcul du cœur (après validation des entrées et tests de propriétés) |
| Les 21 scénarios pièges de l'audit et le test réel « pince » | Premiers éléments du corpus doré et scénario d'acceptation |
| Le retour d'expérience de l'audit | Liste des modes de défaillance à couvrir |

**Non repris** : patches du bridge, bootstrap, dépendance à `blwfish/freecad-mcp`, couches appelées
via `execute_python`, CAM actuelle, licence CC BY-NC.

---

## 5. Plan et moyens (estimation honnête)

| Phase | Durée | Contenu | Critère de sortie |
|---|---|---|---|
| **0 — Cadrage** | 2 à 4 semaines | Exigences validées avec un client pilote réel (sécurité, PLM, volumes) ; choix du LLM sur site ; banc d'essai du noyau ; licence ; preuve de concept bout en bout sur 3 opérations | Document d'exigences signé, PoC qui passe 3 pièges de l'audit |
| **1 — Cœur** | 2 à 3 mois | Vocabulaire d'environ 30 opérations Part/PartDesign, workers conteneurisés, journal d'opérations, vérificateur indépendant, API, corpus doré initial (100 pièces) | Toutes les portes du §3 au vert sur ce périmètre |
| **2 — Entreprise** | 2 à 3 mois | SSO/RBAC, audit, déploiement Kubernetes, observabilité, STEP AP242, paquet air-gap, LLM sur site + banc d'évaluation | Installation hors ligne réussie chez le pilote |
| **3 — Durcissement** | 2 à 3 mois | Test d'intrusion externe, charge, chaos, documentation, support, pilote en conditions réelles | Rapport de pilote favorable, 0 défaut critique |
| **4 — Domaines** | En continu | Assemblages, plans 2D, FEM, robotique… **un par un**, chacun avec son corpus doré | Chaque domaine passe les portes avant d'être activé |

**Total réaliste : 9 à 12 mois avec une équipe de 4 à 6 personnes** (2 développeurs back-end / CAO,
1 sécurité et exploitation, 1 qualité / V&V, 1 spécialiste LLM, plus un ingénieur mécanique à temps
partiel pour le corpus et les règles métier). Seul avec une IA : nettement plus long, et sans les
garanties d'indépendance de vérification qu'un industriel exigera.

---

## 6. Décisions qui te reviennent (bloquantes)

1. **Licence** : passer en licence commerciale ou double licence (ex. AGPL + commerciale, ou
   propriétaire). Sans cela, aucun industriel ne peut l'utiliser.
2. **LLM sur site** : quel modèle et quel matériel (GPU). Cela conditionne la taille du vocabulaire
   d'outils et la qualité de l'expérience.
3. **Client pilote** : un premier utilisateur industriel réel pour fixer les volumes, le PLM cible et
   le niveau de sensibilité des données.
4. **Structure** : entreprise, support, assurance — prérequis d'achat d'un grand groupe.

---

## 7. Lien avec l'audit

- Pour un **outil personnel / open source** : le plan du §7 de l'audit (garder et refonder) reste valable.
- Pour un **produit industriel à grande échelle** : ce document remplace ce plan. Le dépôt actuel
  devient un **prototype de référence** dont on extrait le savoir ; le produit est un nouveau dépôt.
