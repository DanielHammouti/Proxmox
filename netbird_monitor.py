#!/usr/bin/env python3
"""
Surveille les peers NetBird et envoie une alerte Discord (webhook)
quand un peer se déconnecte ou se reconnecte.

Dépendances : aucune (bibliothèque standard uniquement).

Variables d'environnement :
  NETBIRD_TOKEN        Personal Access Token NetBird (obligatoire)
  DISCORD_WEBHOOK_URL  URL du webhook Discord (obligatoire)
  NETBIRD_API_URL      Par défaut https://api.netbird.io (changer si self-hosted)
  WATCH_PEERS          Noms de peers à surveiller, séparés par des virgules
                       (vide = tous les peers). Ex : "pve02,pve01"
  POLL_INTERVAL        Secondes entre deux vérifications (défaut 30)
  STATE_FILE           Fichier d'état (défaut ./netbird_state.json)

Usage :
  python3 netbird_monitor.py          # boucle infinie
  python3 netbird_monitor.py --once   # une seule vérification (pour cron)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_URL = os.environ.get("NETBIRD_API_URL", "https://api.netbird.io").rstrip("/")
TOKEN = os.environ.get("NETBIRD_TOKEN", "")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
WATCH = {p.strip() for p in os.environ.get("WATCH_PEERS", "").split(",") if p.strip()}
INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
STATE_FILE = os.environ.get("STATE_FILE", "netbird_state.json")

# Nombre de vérifications consécutives identiques avant d'alerter
# (évite les faux positifs sur une coupure très brève). 1 = alerte immédiate.
CONFIRMATIONS = int(os.environ.get("CONFIRMATIONS", "2"))


def http_json(url, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
        return json.loads(body) if body else None


def fetch_peers():
    return http_json(
        f"{API_URL}/api/peers",
        headers={
            "Authorization": f"Token {TOKEN}",
            "Accept": "application/json",
        },
    )


def send_discord(content, color):
    payload = {
        "embeds": [
            {
                "description": content,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }
    try:
        http_json(
            WEBHOOK,
            method="POST",
            payload=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "netbird-monitor/1.0",
            },
        )
    except urllib.error.URLError as e:
        print(f"[!] Échec d'envoi Discord : {e}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def check_once(state):
    peers = fetch_peers()
    for p in peers:
        name = p.get("name") or p.get("hostname") or p["id"]
        if WATCH and name not in WATCH:
            continue

        pid = p["id"]
        connected = bool(p.get("connected"))
        ip = p.get("ip", "?")
        last_seen = p.get("last_seen", "?")

        entry = state.get(pid)

        # Première fois qu'on voit ce peer : on enregistre l'état initial
        # et on envoie un message d'information (une seule fois).
        if entry is None:
            state[pid] = {"confirmed": connected, "pending": connected, "count": 0}
            print(f"[init] {name} : {'connecté' if connected else 'déconnecté'}")
            if connected:
                send_discord(f"🔵 **{name}** : état initial **connecté** (`{ip}`)", 0x3498DB)
            else:
                send_discord(
                    f"⚪ **{name}** : état initial **déconnecté** (`{ip}`)\n"
                    f"Dernière activité : `{last_seen}`",
                    0x95A5A6,
                )
            continue

        if connected == entry["confirmed"]:
            # Retour à l'état confirmé : on annule tout changement en attente.
            entry["pending"] = connected
            entry["count"] = 0
            continue

        # État différent du dernier état confirmé : on compte les confirmations.
        if connected == entry["pending"]:
            entry["count"] += 1
        else:
            entry["pending"] = connected
            entry["count"] = 1

        if entry["count"] >= CONFIRMATIONS:
            entry["confirmed"] = connected
            entry["count"] = 0
            if connected:
                msg = f"🟢 **{name}** est de nouveau **connecté** (`{ip}`)"
                color = 0x2ECC71
            else:
                msg = (
                    f"🔴 **{name}** est **déconnecté** (`{ip}`)\n"
                    f"Dernière activité : `{last_seen}`"
                )
                color = 0xE74C3C
            print(msg.replace("\n", " | "))
            send_discord(msg, color)


def main():
    if not TOKEN or not WEBHOOK:
        sys.exit("NETBIRD_TOKEN et DISCORD_WEBHOOK_URL doivent être définis.")

    once = "--once" in sys.argv
    state = load_state()

    while True:
        try:
            check_once(state)
            save_state(state)
        except Exception as e:  # réseau, API indisponible, etc.
            print(f"[!] Erreur : {e}", file=sys.stderr)

        if once:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()