#!/usr/bin/env python3
"""
Surveille trouverunlogement.lescrous.fr et envoie une notification sur le
téléphone (via ntfy.sh) dès qu'un NOUVEAU logement à Marseille apparaît.

Installation :
    pip install requests beautifulsoup4

Notifications :
    1. Installe l'appli gratuite "ntfy" (Android / iPhone).
    2. Abonne-toi au sujet défini dans NTFY_TOPIC ci-dessous
       (choisis un nom unique et difficile à deviner).

Lancement :
    python crous_marseille.py
"""

import json
import os
import sys
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------- RÉGLAGES ----------------
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or "crous-marseille-CHANGE-MOI-123"   # <- mets un nom unique (ou secret GitHub)
INTERVALLE_MINUTES = 5                           # fréquence de vérification
VILLE = re.compile(r"\b13\d{3}\b")  # code postal 13xxx (tout le département des Bouches-du-Rhône)
# ------------------------------------------

BASE = "https://trouverunlogement.lescrous.fr"
FICHIER_VUS = Path(__file__).with_name("logements_vus.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (veille logement perso)"}


def id_outil_annee() -> str:
    """L'identifiant de l'année (47 pour 2026-2027) change chaque année : on le lit sur l'accueil."""
    html = requests.get(BASE, headers=HEADERS, timeout=30).text
    m = re.search(r"/tools/(\d+)/search", html)
    return m.group(1) if m else "47"


def charger_page(outil: str, page: int) -> BeautifulSoup:
    """Télécharge une page de résultats, avec 3 essais si le site répond mal."""
    url = f"{BASE}/tools/{outil}/search?page={page}"
    soup = BeautifulSoup("", "html.parser")
    for essai in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            r.encoding = "utf-8"  # sinon les accents ("trouvés") sont mal lus
            soup = BeautifulSoup(r.text, "html.parser")
            if soup.select('a[href*="/accommodations/"]'):
                return soup
        except requests.RequestException:
            pass
        time.sleep(5)
    return soup


def lire_toutes_les_pages(outil: str, resultats: dict) -> int:
    """Ajoute dans `resultats` les logements de toutes les pages. Renvoie le total annoncé."""
    premiere = charger_page(outil, 1)
    texte = " ".join(premiere.get_text(" ", strip=True).split())
    m_total = re.search(r"(\d+)\s*logements?\s+trouv", texte)
    total = int(m_total.group(1)) if m_total else 0

    # Nombre de pages : d'après les liens de pagination, le titre, ou le total (24 par page)
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", str(premiere))]
    titre = premiere.title.get_text(" ") if premiere.title else ""
    m_sur = re.search(r"sur\s+(\d+)", " ".join(titre.split()))
    if m_sur:
        pages.append(int(m_sur.group(1)))
    nb_pages = max(pages + [-(-total // 24), 1])

    for page in range(1, nb_pages + 1):
        soup = premiere if page == 1 else charger_page(outil, page)
        for a in soup.select('a[href*="/accommodations/"]'):
            m = re.search(r"/accommodations/(\d+)", a["href"])
            if not m or m.group(1) in resultats:
                continue
            bloc = a.find_parent("li") or a.parent
            texte_bloc = " ".join(bloc.get_text(" ", strip=True).split())
            lien = BASE + a["href"] if a["href"].startswith("/") else a["href"]
            resultats[m.group(1)] = {"texte": texte_bloc, "lien": lien}
        time.sleep(1)  # on reste poli avec le serveur
    return total


def logements_disponibles(outil: str) -> tuple[dict, int]:
    """Le site mélange parfois l'ordre entre les pages (doublons / oublis) :
    on refait plusieurs passages et on cumule jusqu'à avoir tout le monde."""
    resultats, total = {}, 0
    for passage in range(5):
        total = lire_toutes_les_pages(outil, resultats) or total
        if total and len(resultats) >= total:
            break
        time.sleep(10)
    return resultats, total


def notifier(titre: str, message: str, lien: str | None = None) -> None:
    headers = {"Title": titre.encode("utf-8"), "Priority": "high", "Tags": "house"}
    if lien:
        headers["Click"] = lien
    requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"), headers=headers, timeout=30)


def charger_vus() -> set:
    try:
        return set(json.loads(FICHIER_VUS.read_text()))
    except Exception:
        return set()


def verifier(vus: set) -> set:
    outil = id_outil_annee()
    tous, total_annonce = logements_disponibles(outil)
    marseille = {i: d for i, d in tous.items() if VILLE.search(d["texte"])}
    nouveaux = {i: d for i, d in marseille.items() if i not in vus}

    heure = datetime.now().strftime("%H:%M")
    print(f"[{heure}] {len(tous)}/{total_annonce} logements lus en France, {len(marseille)} dans le 13, {len(nouveaux)} nouveau(x)")

    for i, d in nouveaux.items():
        print("  ->", d["texte"][:150], d["lien"])
        notifier("Logement CROUS dans le 13 !", d["texte"][:300], d["lien"])

    vus |= set(marseille)
    FICHIER_VUS.write_text(json.dumps(sorted(vus)))
    return vus


if __name__ == "__main__":
    # "--une-fois" : une seule vérification puis arrêt (utilisé par GitHub Actions toutes les 30 min)
    if "--une-fois" in sys.argv:
        verifier(charger_vus())
        sys.exit(0)

    notifier("Veille CROUS lancée", "Tu recevras un message dès qu'un logement apparaît dans le 13 (code postal 13xxx).")
    vus = charger_vus()
    while True:
        try:
            vus = verifier(vus)
        except Exception as e:
            print("Erreur :", e)
        time.sleep(INTERVALLE_MINUTES * 60)
