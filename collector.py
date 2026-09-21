import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://yata.yt/api/v1/travel/export/"
PROMETHEUS_URL = "https://api.prombot.co.uk/api/travel"
DATA_DIR = Path("data")
LATEST = DATA_DIR / "latest.json"
HISTORY = DATA_DIR / "history.csv"
TRANSITIONS = DATA_DIR / "transitions.csv"
STATE = DATA_DIR / "collector_state.json"
OBSERVATIONS = DATA_DIR / "provider_observations.jsonl"
HEALTH = DATA_DIR / "source_health.csv"

WATCH = {
    "South Africa": ["Raw Ivory", "Xanax", "Smoke Grenade", "Lion Plushie", "African Violet"],
    "Japan": ["Xanax"],
    "Canada": ["Xanax"],
    "United Kingdom": ["Xanax"],
    "UAE": ["Tribulus Omanense", "Camel Plushie", "Natural Pearls"],
    "Switzerland": ["Neumune Tablet"],
    "Hawaii": ["Shark Fin"],
    "China": ["Blank Casino Chips", "Panda Plushie", "Pangolin Scales", "Peony"],
}
ITEM_IDS = {
    "China": {327: "Blank Casino Chips"},
}
ITEM_ALIASES = {
    "China": {
        "blank casino chips": "Blank Casino Chips",
        "blank tokens": "Blank Casino Chips",
    },
}
COUNTRY_CODES = {
    "sou": "South Africa", "jap": "Japan", "can": "Canada",
    "uni": "United Kingdom", "uae": "UAE", "swi": "Switzerland",
    "haw": "Hawaii", "chi": "China",
}
HISTORY_FIELDS = ["poll_timestamp", "observation_timestamp", "country", "item", "stock", "source", "source_update"]
TRANSITION_FIELDS = ["lower_bound", "upper_bound", "country", "item", "from_stock", "to_stock", "type", "source", "source_update"]
HEALTH_FIELDS = ["collection_timestamp", "source", "success", "parsed_items", "fresh_countries", "error"]


def fetch_data(url=URL):
    request = Request(url, headers={
        "Accept": "application/json", "User-Agent": "TornForeignStockCollector/5.0",
    })
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def nonnegative_integer(value):
    # Missing, booleans, negative and fractional quantities are not zero.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(value)
        return number if number >= 0 else None
    except ValueError:
        return None


def source_time(value):
    value = nonnegative_integer(value)
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def canonical_item(country, item):
    """Resolve watched items by stable ID first, then known aliases/name."""
    if country not in WATCH or not isinstance(item, dict):
        return None

    # Blank Casino Chips was renamed from Blank Tokens. Some providers may
    # expose the stable Torn item ID even when the display name is stale.
    for field in ("id", "item_id", "itemID", "itemId"):
        item_id = nonnegative_integer(item.get(field))
        canonical = ITEM_IDS.get(country, {}).get(item_id)
        if canonical is not None:
            return canonical

    name = str(item.get("name", "")).strip().lower()
    canonical = ITEM_ALIASES.get(country, {}).get(name)
    if canonical is not None:
        return canonical

    wanted = {name.lower(): name for name in WATCH[country]}
    return wanted.get(name)


def parse_stock(data):
    result = {f"{country}|{item}": None for country, items in WATCH.items() for item in items}
    updates = {}
    if not isinstance(data, dict) or not isinstance(data.get("stocks"), dict):
        raise ValueError("Response has no stocks object")
    for code, entry in data["stocks"].items():
        country = COUNTRY_CODES.get(code)
        if country not in WATCH or not isinstance(entry, dict):
            continue
        if source_time(entry.get("update")) is not None:
            updates[country] = int(entry["update"])
        items = entry.get("stocks", [])
        if not isinstance(items, list):
            continue
        for item in items:
            canonical = canonical_item(country, item)
            if canonical is not None:
                result[f"{country}|{canonical}"] = nonnegative_integer(item.get("quantity"))
    return result, updates


def load_json(path, default):
    if not path.exists():
        return default
    # Corrupt persisted state must not silently reset confirmation safeguards.
    return json.loads(path.read_text(encoding="utf-8"))


def load_previous():
    return load_json(LATEST, None)


def append_csv(path, fields, row):
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def log_raw(provider, payload, timestamp):
    # Keep only watched observations, never the full provider response.
    # An absent item has no entry; unknown quantity/update remains null, not zero.
    observations = []
    stocks = payload.get("stocks", {}) if isinstance(payload, dict) else {}
    for code, entry in stocks.items() if isinstance(stocks, dict) else []:
        country = COUNTRY_CODES.get(code)
        if country not in WATCH or not isinstance(entry, dict) or not isinstance(entry.get("stocks"), list):
            continue
        for item in entry["stocks"]:
            canonical = canonical_item(country, item)
            if canonical is None:
                continue
            observation = {
                "country": country, "item": canonical,
                "source_update": entry.get("update"), "quantity": item.get("quantity"),
            }
            if "nextRestock" in item:
                observation["nextRestock"] = item["nextRestock"]
            observations.append(observation)
    with OBSERVATIONS.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"source": provider, "collection_timestamp": timestamp,
                               "observations": observations}, ensure_ascii=False) + "\n")


def process(providers, previous, state, timestamp):
    """Select country snapshots and confirm only advancing item observations."""
    previous = previous or {}
    stock = {f"{country}|{item}": previous.get("stock", {}).get(f"{country}|{item}")
             for country, items in WATCH.items() for item in items}
    updates = dict(previous.get("source_updates", {}))
    observed = dict(previous.get("observed_at", {}))
    sources = dict(previous.get("sources", {}))
    item_states = state.setdefault("items", {})
    seen = state.setdefault("provider_updates", {})
    for country, items in WATCH.items():
        available = [(name, values, times[country]) for name, (values, times) in providers.items()
                     if country in times and any(values.get(f"{country}|{item}") is not None for item in items)]
        if not available:
            continue
        # Stable ordering favors YATA when timestamps tie.
        provider, values, update = max(available, key=lambda entry: entry[2])
        country_previous = nonnegative_integer(updates.get(country)) or 0
        if update < country_previous:
            continue
        for item in items:
            key = f"{country}|{item}"
            value = values.get(key)
            entry = item_states.setdefault(key, {
                "confirmed": stock[key], "last_update": country_previous,
                "confirmed_at": observed.get(country) or previous.get("timestamp"),
            })
            last_update = entry["last_update"]
            if value is None or update <= last_update:
                continue
            old = entry["confirmed"]
            stock[key] = value
            append_csv(HISTORY, HISTORY_FIELDS, {
                "poll_timestamp": timestamp, "observation_timestamp": source_time(update),
                "country": country, "item": item, "stock": value,
                "source": provider, "source_update": update,
            })
            changed = old is not None and (old > 0) != (value > 0)
            if changed:
                candidate = entry.get("candidate")
                if candidate is None:
                    candidate = {"update": update, "quantity": value, "source": provider,
                                 "lower_bound": entry["confirmed_at"], "from_stock": old}
                    entry["candidate"] = candidate
                agreeing = [name for name, quantities, source_update in available
                            if source_update > last_update
                            and source_update > seen.get(name, {}).get(country, 0)
                            and quantities.get(key) is not None
                            and (quantities[key] > 0) == (value > 0)]
                if update > candidate["update"] or len(agreeing) >= 2:
                    append_csv(TRANSITIONS, TRANSITION_FIELDS, {
                        "lower_bound": candidate["lower_bound"],
                        "upper_bound": source_time(candidate["update"]),
                        "country": country, "item": item,
                        "from_stock": candidate["from_stock"], "to_stock": candidate["quantity"],
                        "type": "restock" if value > 0 else "stockout",
                        "source": candidate["source"], "source_update": candidate["update"],
                    })
                    entry["confirmed"] = value
                    entry["confirmed_at"] = source_time(update)
                    entry.pop("candidate", None)
            else:
                entry["confirmed"] = value
                entry["confirmed_at"] = source_time(update)
                entry.pop("candidate", None)
            entry["last_update"] = update
        updates[country] = update
        observed[country] = source_time(update)
        sources[country] = provider
    for provider, (_, times) in providers.items():
        watermark = seen.setdefault(provider, {})
        for country, update in times.items():
            watermark[country] = max(update, watermark.get(country, 0))
    used = set(sources.values())
    source = next(iter(used)) if len(used) == 1 else "mixed" if used else previous.get("source", "YATA")
    return {"timestamp": timestamp, "source": source,
            "source_url": PROMETHEUS_URL if source == "Prometheus" else URL,
            "travel_slots": 28, "source_updates": updates, "observed_at": observed,
            "stock": stock, "sources": sources,
            "source_urls": {"YATA": URL, "Prometheus": PROMETHEUS_URL}}


def main():
    DATA_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    previous = load_previous()
    state = load_json(STATE, {})
    providers = {}
    urls = {"YATA": URL}
    if os.environ.get("ENABLE_PROMETHEUS", "false").lower() in {"true", "1", "yes"}:
        urls["Prometheus"] = PROMETHEUS_URL
    for provider, url in urls.items():
        health = dict(collection_timestamp=timestamp, source=provider, success=False,
                      parsed_items=0, fresh_countries=0, error="")
        try:
            payload = fetch_data(url)
            log_raw(provider, payload, timestamp)
            values, updates = parse_stock(payload)
            health["parsed_items"] = sum(value is not None for value in values.values())
            if not any(value is not None and key.split("|", 1)[0] in updates for key, value in values.items()):
                raise ValueError("No watched quantities with valid source timestamps")
            health["fresh_countries"] = sum(update > state.get("provider_updates", {}).get(provider, {}).get(country, 0)
                                            for country, update in updates.items())
            providers[provider] = (values, updates)
            health["success"] = True
        except (OSError, ValueError, RuntimeError) as error:
            health["error"] = f"{type(error).__name__}: {error}"
            print(f"{provider} failed: {health['error']}")
        append_csv(HEALTH, HEALTH_FIELDS, health)
    if not providers:
        raise RuntimeError("All enabled sources failed; previous state preserved")
    snapshot = process(providers, previous, state, timestamp)
    write_json(STATE, state)
    write_json(LATEST, snapshot)
    print(f"Collected from {', '.join(providers)}")


if __name__ == "__main__":
    main()
