import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://yata.yt/api/v1/travel/export/"

DATA_DIR = Path("data")
LATEST = DATA_DIR / "latest.json"
HISTORY = DATA_DIR / "history.csv"
TRANSITIONS = DATA_DIR / "transitions.csv"

WATCH = {
    "South Africa": [
        "Raw Ivory",
        "Xanax",
        "Smoke Grenade",
        "Lion Plushie",
        "African Violet",
    ],
    "Japan": ["Xanax"],
    "Canada": ["Xanax"],
    "United Kingdom": ["Xanax"],
    "UAE": [
        "Tribulus Omanense",
        "Camel Plushie",
    ],
    "Switzerland": ["Neumune Tablet"],
    "China": [
        "Panda Plushie",
        "Peony",
    ],
}

COUNTRY_CODES = {
    "sou": "South Africa",
    "jap": "Japan",
    "can": "Canada",
    "uni": "United Kingdom",
    "uae": "UAE",
    "swi": "Switzerland",
    "chi": "China",
}


def fetch_data():
    request = Request(
        URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "TornForeignStockCollector/4.0",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_stock(data):
    result = {
        f"{country}|{item}": None
        for country, items in WATCH.items()
        for item in items
    }

    source_updates = {}

    stocks = data.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError("YATA response has no stocks object")

    for code, country_data in stocks.items():
        country = COUNTRY_CODES.get(code)

        if country not in WATCH:
            continue

        if not isinstance(country_data, dict):
            continue

        source_updates[country] = country_data.get("update")

        items = country_data.get("stocks", [])

        if not isinstance(items, list):
            continue

        wanted = {
            name.lower(): name
            for name in WATCH[country]
        }

        for item in items:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            canonical = wanted.get(name.lower())

            if canonical is None:
                continue

            try:
                quantity = int(item.get("quantity", 0))
            except (TypeError, ValueError):
                continue

            result[f"{country}|{canonical}"] = quantity

    return result, source_updates


def load_previous():
    if not LATEST.exists():
        return None

    try:
        return json.loads(
            LATEST.read_text(encoding="utf-8")
        )
    except Exception:
        return None


def append_csv(path, fields, row):
    exists = path.exists()

    with path.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        if not exists:
            writer.writeheader()

        writer.writerow(row)


def source_time(value):
    if value is None:
        return None

    try:
        return datetime.fromtimestamp(
            int(value),
            tz=timezone.utc,
        ).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def main():
    DATA_DIR.mkdir(exist_ok=True)

    poll_timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    data = fetch_data()
    fetched_stock, source_updates = parse_stock(data)

    found = sum(
        value is not None
        for value in fetched_stock.values()
    )

    missing = [
        key
        for key, value in fetched_stock.items()
        if value is None
    ]

    print(
        f"Parsed {found}/{len(fetched_stock)} watched items"
    )

    if missing:
        print(
            "Missing targets: "
            + ", ".join(missing)
        )

    # Ved kraftig parse-/kildesvikt endrer vi ingenting.
    if found < 8:
        raise RuntimeError(
            f"Only parsed {found}/{len(fetched_stock)} watched "
            "items. Previous state preserved."
        )

    previous = load_previous()

    previous_stock = (
        previous.get("stock", {})
        if previous
        else {}
    )

    previous_updates = (
        previous.get("source_updates", {})
        if previous
        else {}
    )

    previous_observed = (
        previous.get("observed_at", {})
        if previous
        else {}
    )

    # Bevar siste gyldige verdi dersom én vare mangler
    # i den nye YATA-responsen.
    stock = {}

    for key, value in fetched_stock.items():
        if value is None and key in previous_stock:
            stock[key] = previous_stock[key]
        else:
            stock[key] = value

    # Finn hvilke land som faktisk har fått ny
    # kildeobservasjon siden forrige polling.
    fresh_countries = set()

    for country in WATCH:
        new_update = source_updates.get(country)
        old_update = previous_updates.get(country)

        if new_update is None:
            continue

        if previous is None or new_update != old_update:
            fresh_countries.add(country)

    observed_at = dict(previous_observed)

    for country in fresh_countries:
        observed_at[country] = (
            source_time(source_updates.get(country))
            or poll_timestamp
        )

    # Første snapshot skal etablere state.
    # Senere snapshots skal bare behandle land med
    # en faktisk ny source_update som nye observasjoner.
    for key, new_value in fetched_stock.items():
        country, item = key.split("|", 1)

        if country not in fresh_countries:
            continue

        # Manglende verdi er IKKE 0 og skal ikke
        # registreres som en observasjon.
        if new_value is None:
            continue

        observation_time = (
            source_time(source_updates.get(country))
            or poll_timestamp
        )

        append_csv(
            HISTORY,
            [
                "poll_timestamp",
                "observation_timestamp",
                "country",
                "item",
                "stock",
                "source",
                "source_update",
            ],
            {
                "poll_timestamp": poll_timestamp,
                "observation_timestamp": observation_time,
                "country": country,
                "item": item,
                "stock": new_value,
                "source": "YATA",
                "source_update":
                    source_updates.get(country, ""),
            },
        )

    # Registrer bare ekte 0 <-> positiv-overganger
    # fra nye kildeobservasjoner.
    if previous:
        for key, new_value in fetched_stock.items():
            country, item = key.split("|", 1)

            if country not in fresh_countries:
                continue

            if new_value is None:
                continue

            old_value = previous_stock.get(key)

            if old_value is None:
                continue

            transition = None

            if old_value > 0 and new_value == 0:
                transition = "stockout"

            elif old_value == 0 and new_value > 0:
                transition = "restock"

            if transition:
                lower_bound = (
                    previous_observed.get(country)
                    or previous.get("timestamp")
                )

                upper_bound = (
                    source_time(source_updates.get(country))
                    or poll_timestamp
                )

                append_csv(
                    TRANSITIONS,
                    [
                        "lower_bound",
                        "upper_bound",
                        "country",
                        "item",
                        "from_stock",
                        "to_stock",
                        "type",
                        "source",
                        "source_update",
                    ],
                    {
                        "lower_bound": lower_bound,
                        "upper_bound": upper_bound,
                        "country": country,
                        "item": item,
                        "from_stock": old_value,
                        "to_stock": new_value,
                        "type": transition,
                        "source": "YATA",
                        "source_update":
                            source_updates.get(country, ""),
                    },
                )

    # Hvis et land mangler source_update midlertidig,
    # bevar forrige gyldige source_update.
    merged_updates = dict(previous_updates)

    for country, value in source_updates.items():
        if value is not None:
            merged_updates[country] = value

    snapshot = {
        "timestamp": poll_timestamp,
        "source": "YATA",
        "source_url": URL,
        "travel_slots": 28,
        "source_updates": merged_updates,
        "observed_at": observed_at,
        "stock": stock,
    }

    LATEST.write_text(
        json.dumps(
            snapshot,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"Collected {found}/{len(fetched_stock)} watched items"
    )

    print(
        f"Fresh countries: "
        f"{len(fresh_countries)}/{len(WATCH)}"
    )

    if fresh_countries:
        print(
            "New source observations: "
            + ", ".join(sorted(fresh_countries))
        )
    else:
        print(
            "YATA source timestamps unchanged; "
            "no duplicate observations recorded."
        )


if __name__ == "__main__":
    main()
