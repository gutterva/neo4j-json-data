"""
Valorant TGN Data Collection Script
====================================
Collects up to 100 deduplicated matches across 10 players using
Henrik's unofficial Valorant API (api.henrikdev.xyz).

Usage:
    Set your API key and player list below, then run:
        python collect_valorant_data.py

Output:
    matches.json — list of parsed match dicts, incrementally saved.

Rate limit: 30 req/min (free tier).
  - Base delay: 2.5s between requests
  - 429 handling: exponential backoff (10s, 20s, 40s) with up to 3 retries
Region: ap (Asia-Pacific)
"""

import time
import json
import os
import requests

# ─────────────────────────────────────────────
# CONFIG — edit these before running
# ─────────────────────────────────────────────
API_KEY = "HDEV-4a691ee9-10e7-4dd9-94c6-396abb033153"

PLAYERS = [
    {"name": "再化降水驻守", "tag": "0817"},
    {"name": "路明非同款衰仔", "tag": "CJX17"},
    {"name": "T1 Oner", "tag": "flowe"},
    {"name": "S1Mon", "tag": "625"},
    {"name": "AMD Champ", "tag": "ROGUE"},
    {"name": "axildeus",  "tag": "LVGOD"},
    {"name": "Fishyy",  "tag": "minji"},
    {"name": "Yukiri",  "tag": "XXXX"},
    {"name": "亚撒西",  "tag": "1105"},
    {"name": "OFC Autre", "tag": "TMS"},
]

REGION = "ap"
MATCH_LIMIT = 100
OUTPUT_FILE = "matches.json"
REQUEST_DELAY = 2.5      # seconds between every request (safe under 30 req/min)
MAX_RETRIES = 3          # max retry attempts on 429
BACKOFF_BASE = 10        # initial backoff in seconds (doubles each retry: 10, 20, 40)
# ─────────────────────────────────────────────

HEADERS = {"Authorization": API_KEY}
BASE_URL = "https://api.henrikdev.xyz/valorant"


def api_get(url: str, params: dict = None) -> requests.Response | None:
    """
    Make a GET request with:
      - 2.5s base delay after every request
      - Exponential backoff on 429 (10s, 20s, 40s), up to MAX_RETRIES retries
      - Returns None if all retries exhausted or a non-recoverable error occurs
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            time.sleep(REQUEST_DELAY)

            if resp.status_code == 429:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"  [429] Rate limited. Waiting {wait}s before retry "
                      f"(attempt {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(wait)
                continue  # retry

            return resp  # success or non-429 failure — let caller handle

        except requests.exceptions.RequestException as e:
            print(f"  [ERROR] Request exception: {e}")
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
            else:
                return None

    print(f"  [ERROR] All {MAX_RETRIES} retries exhausted for {url}")
    return None


def get_puuid(name: str, tag: str) -> str | None:
    """Resolve player name+tag to PUUID via v1 account endpoint."""
    url = f"{BASE_URL}/v1/account/{name}/{tag}"
    resp = api_get(url)
    if resp is None or resp.status_code != 200:
        print(f"  [WARN] Could not resolve PUUID for {name}#{tag} "
              f"(status {resp.status_code if resp else 'no response'})")
        return None
    puuid = resp.json().get("data", {}).get("puuid")
    if not puuid:
        print(f"  [WARN] No PUUID in response for {name}#{tag}")
    return puuid


def get_match_ids(puuid: str, region: str) -> list[str]:
    """
    Fetch up to 10 recent competitive match IDs for a PUUID.
    v3 endpoint ignores pagination offset — always returns the latest 10.
    """
    url = f"{BASE_URL}/v3/by-puuid/matches/{region}/{puuid}"
    params = {"mode": "competitive", "size": 10}
    resp = api_get(url, params=params)
    if resp is None or resp.status_code != 200:
        print(f"  [WARN] Match list fetch failed "
              f"(status {resp.status_code if resp else 'no response'})")
        return []
    ids = []
    for m in resp.json().get("data", []):
        if m.get("metadata") and m["metadata"].get("matchid"):
            ids.append(m["metadata"]["matchid"])
    return ids


def get_match_detail(match_id: str) -> dict | None:
    """Fetch full match detail by match ID."""
    url = f"{BASE_URL}/v2/match/{match_id}"
    resp = api_get(url)
    if resp is None or resp.status_code != 200:
        print(f"  [WARN] Match detail fetch failed for {match_id} "
              f"(status {resp.status_code if resp else 'no response'})")
        return None
    return resp.json().get("data")


def parse_match(raw: dict) -> dict | None:
    """
    Parse a raw match detail dict into the TGN-ready format.

    Returns a dict with:
        match_id : str
        rounds   : list of { round_num, winning_team }
        kills    : list of kill event dicts
    Returns None if the match is malformed or missing required fields.
    """
    try:
        metadata = raw.get("metadata", {})
        match_id = metadata.get("matchId") or metadata.get("matchid")
        if not match_id:
            return None

        # ── Rounds ──────────────────────────────────────────────────────────
        rounds = []
        for r in raw.get("rounds", []):
            winning_team = r.get("winning_team") or r.get("winningTeam")
            if winning_team is None:
                continue
            rounds.append({
                "round_num": r.get("round_num", r.get("roundNum")),
                "winning_team": winning_team,
            })

        # ── Kills ────────────────────────────────────────────────────────────
        kills = []
        for k in raw.get("kills", []):
            kills.append({
                "round": k.get("round"),
                "kill_time_in_round": k.get("kill_time_in_round",
                                            k.get("killTimeInRound")),
                "killer_puuid": k.get("killer_puuid",
                                      k.get("killer", {}).get("puuid")),
                "killer_team": k.get("killer_team",
                                     k.get("killer", {}).get("team")),
                "victim_puuid": k.get("victim_puuid",
                                      k.get("victim", {}).get("puuid")),
                "victim_team": k.get("victim_team",
                                     k.get("victim", {}).get("team")),
                "damage_weapon_name": k.get("damage_weapon_name",
                                            k.get("damageWeaponName")),
                "assistants": k.get("assistants", []),
                "player_locations_on_kill": k.get("player_locations_on_kill",
                                                   k.get("playerLocationsOnKill", [])),
            })

        if not rounds or not kills:
            print(f"  [SKIP] Match {match_id} has no rounds or kills — skipping.")
            return None

        return {"match_id": match_id, "rounds": rounds, "kills": kills}

    except Exception as e:
        print(f"  [ERROR] parse_match failed: {e}")
        return None


def load_existing(output_file: str) -> tuple[list, set]:
    """Load existing matches.json for incremental saves."""
    if os.path.exists(output_file):
        with open(output_file, "r") as f:
            existing = json.load(f)
        seen_ids = {m["match_id"] for m in existing if "match_id" in m}
        print(f"[INFO] Loaded {len(existing)} existing matches from {output_file}")
        return existing, seen_ids
    return [], set()


def save(matches: list, output_file: str) -> None:
    """Incrementally overwrite output file with current match list."""
    with open(output_file, "w") as f:
        json.dump(matches, f, indent=2)


def collect(players: list[dict], region: str, match_limit: int,
            output_file: str) -> None:
    all_matches, seen_ids = load_existing(output_file)

    for player in players:
        if len(all_matches) >= match_limit:
            print(f"[INFO] Reached {match_limit} matches. Stopping.")
            break

        name, tag = player["name"], player["tag"]
        print(f"\n[PLAYER] {name}#{tag}")

        puuid = get_puuid(name, tag)
        if not puuid:
            continue
        print(f"  PUUID: {puuid}")

        match_ids = get_match_ids(puuid, region)
        print(f"  Found {len(match_ids)} match IDs")

        for match_id in match_ids:
            if len(all_matches) >= match_limit:
                break

            if match_id in seen_ids:
                print(f"  [DUP]  {match_id} — skipping")
                continue

            print(f"  [FETCH] {match_id}")
            raw = get_match_detail(match_id)
            if raw is None:
                continue

            parsed = parse_match(raw)
            if parsed is None:
                continue

            all_matches.append(parsed)
            seen_ids.add(match_id)
            save(all_matches, output_file)
            print(f"  [SAVED] {match_id} | total={len(all_matches)}")

    print(f"\n[DONE] Collected {len(all_matches)} matches → {output_file}")


if __name__ == "__main__":
    collect(
        players=PLAYERS,
        region=REGION,
        match_limit=MATCH_LIMIT,
        output_file=OUTPUT_FILE,
    )