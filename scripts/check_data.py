#!/usr/bin/env python3
"""Valide les fichiers de données tenus à la main avant commit.

Le serveur lit `evacuations.json` et `situation.json` derrière un `except`
silencieux : un JSON cassé ne lève rien, il vide simplement le panneau en
production. Ce script est le garde-fou — il échoue bruyamment, lui.

    python3 scripts/check_data.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAMPS = ("commune", "dept", "lat", "lon", "population", "statut",
          "annonce", "source", "source_url")
RE_DEPT = re.compile(r"^.+ \(\d{2,3}[AB]?\)$")
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

erreurs = []


def err(fichier, ou, msg):
    erreurs.append(f"{fichier} · {ou} : {msg}")


def lignes_entrees(brut):
    """Ligne de départ de chaque entrée : {(clé de tableau, index): ligne}.

    Le validateur travaille sur l'objet déjà parsé, qui a perdu les numéros de
    ligne — d'où des messages en `retours[41]`, impossibles à retrouver dans un
    fichier de 900 lignes (issue #21). On repère donc ici la ligne où commence
    chaque entrée, pour pointer l'erreur là où elle se corrige.
    """
    lignes, cle, idx, prof, dernier = {}, None, 0, 0, None
    i, ligne, n = 0, 1, len(brut)
    while i < n:
        ch = brut[i]
        if ch == "\n":
            ligne += 1
        elif ch == '"':
            j = i + 1
            while j < n and brut[j] != '"':
                j += 2 if brut[j] == "\\" else 1
            if prof == 1:
                dernier = brut[i + 1:j]      # dernière clé vue à la racine
            ligne += brut[i:j].count("\n")
            i = j + 1
            continue
        elif ch in "{[":
            if ch == "[" and prof == 1:
                cle, idx = dernier, 0
            prof += 1
            if ch == "{" and prof == 3 and cle:
                lignes[(cle, idx)] = ligne
                idx += 1
        elif ch in "}]":
            prof -= 1
            if prof == 1:
                cle = None
        i += 1
    return lignes


def charge(nom):
    """Signale précisément les deux fautes de frappe qui cassent le JSON."""
    chemin = ROOT / nom
    if not chemin.exists():
        # Chemin complet : sans lui on ignore où le script attend le fichier.
        err(chemin, "fichier", "introuvable")
        return None, {}
    brut = chemin.read_text(encoding="utf-8")
    for i, ligne in enumerate(brut.split("\n"), 1):
        if re.match(r"^\s*(//|/\*|#)", ligne):
            err(nom, f"ligne {i}", "commentaire : le JSON n'en accepte pas")
    try:
        return json.loads(brut), lignes_entrees(brut)
    except json.JSONDecodeError as exc:
        indice = ""
        if "trailing comma" in str(exc).lower():
            indice = " (virgule traînante avant une fermeture)"
        err(nom, f"ligne {exc.lineno}", f"JSON invalide{indice} — {exc.msg}")
        return None, {}


def texte_au_lieu_de(v, attendu):
    """Message dédié au piège des nombres restés entre guillemets.

    Une conversion CSV produit facilement "43.4049" ou "null" : la valeur a
    l'air juste à l'œil, seul le type cloche. Le dire explicitement fait gagner
    la demi-heure de relecture (issue #20).
    """
    if isinstance(v, str):
        return f"texte détecté ({v!r}) — {attendu}, sans guillemets"
    return None


def verifie_entrees(nom, cle, entrees, lignes):
    for i, e in enumerate(entrees):
        # Le numéro de ligne prime : c'est la seule info qui mène droit à
        # l'entrée fautive dans un fichier de plusieurs centaines de lignes.
        pos = lignes.get((cle, i))
        ou = f"{cle}[{i}]" + (f" ligne {pos}" if pos else "")
        if not isinstance(e, dict):
            err(nom, ou, "n'est pas un objet")
            continue
        if set(e) - set(CHAMPS):
            inconnus = ", ".join(sorted(set(e) - set(CHAMPS)))
            err(nom, ou, f"champ(s) hors format : {inconnus}. "
                         "Pas de séparateur dans les tableaux, `dept` porte le regroupement")
            continue
        if e.get("commune"):
            ou += f" (commune « {e['commune']} »)"
        for champ in ("commune", "dept", "statut", "source", "source_url"):
            if not e.get(champ):
                err(nom, ou, f"`{champ}` manquant — pas un chiffre sans source")
        if not RE_DEPT.match(str(e.get("dept", ""))):
            err(nom, ou, f"`dept` attendu au format \"Nom (NN)\", reçu {e.get('dept')!r}")
        for champ in ("lat", "lon"):
            v = e.get(champ)
            if not isinstance(v, (int, float)):
                err(nom, ou, f"`{champ}` : " + (texte_au_lieu_de(v, "un nombre attendu")
                    or "doit être un nombre (mairie geo.api.gouv.fr)"))
        if isinstance(e.get("lat"), (int, float)) and not 41 <= e["lat"] <= 52:
            err(nom, ou, f"`lat` {e['lat']} hors métropole")
        if isinstance(e.get("lon"), (int, float)) and not -5.5 <= e["lon"] <= 10:
            err(nom, ou, f"`lon` {e['lon']} hors métropole")
        # Absent ≠ null : un champ oublié passait pour un « chiffre non publié »
        # assumé, alors que c'est une saisie incomplète (issue #23).
        if "population" not in e:
            err(nom, ou, "`population` manquant — mettre le chiffre INSEE, "
                         "ou null explicitement si la préfecture n'en publie pas")
        else:
            pop = e["population"]
            if pop is not None and not isinstance(pop, int):
                err(nom, ou, "`population` : " + (texte_au_lieu_de(pop, "un entier ou null attendu")
                    or "doit être un entier ou null — jamais une estimation"))
        if not RE_DATE.match(str(e.get("annonce", ""))):
            err(nom, ou, "`annonce` attendue en AAAA-MM-JJ (précision libre entre parenthèses)")
        if not str(e.get("source_url", "")).startswith("http"):
            err(nom, ou, "`source_url` doit être un lien direct vers le communiqué")


def main():
    evac, lignes = charge("evacuations.json")
    if evac is not None:
        for cle in ("evacuations", "historique", "retours"):
            if cle not in evac:
                if cle == "evacuations":
                    err("evacuations.json", "racine", "clé `evacuations` absente")
                continue
            if not isinstance(evac[cle], list):
                err("evacuations.json", cle, "doit être un tableau")
                continue
            verifie_entrees("evacuations.json", cle, evac[cle], lignes)
        if not RE_DATE.match(str(evac.get("updated", ""))):
            err("evacuations.json", "racine", "`updated` attendu en AAAA-MM-JJThh:mm:ssZ")

    charge("situation.json")  # validité JSON seulement : le schéma y est libre

    if erreurs:
        print(f"✗ {len(erreurs)} problème(s) :\n", file=sys.stderr)
        for e in erreurs:
            print(f"  - {e}", file=sys.stderr)
        print("\nVoir le format attendu dans .github/PULL_REQUEST_TEMPLATE.md", file=sys.stderr)
        return 1

    n = sum(len(evac.get(c, [])) for c in ("evacuations", "historique", "retours")) if evac else 0
    print(f"✓ données valides — {n} entrées vérifiées")
    return 0


if __name__ == "__main__":
    sys.exit(main())
