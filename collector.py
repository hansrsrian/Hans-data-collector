import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://torn-intel.com/api/v1/public/foreign-stock"

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
    "China": ["Blank Casino Chips"],
}


def fetch_data():
    api_key = os.environ.get("TORN_INTEL_KEY")

    if not api_key:
        raise RuntimeError(
            "TORN_INTEL_KEY is not available in the environment"
        )

    request = Request(
        URL,
        headers={
            "X-Torn-Intel-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "TornForeignStockCollector/2.0",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def normalize_country(name):
    aliases = {
        "SA": "South Africa",
        "South Africa": "South Africa",
        "Japan": "Japan",
        "Canada": "Canada",
        "UK": "United Kingdom",
        "United Kingdom": "United Kingdom",
        "UAE": "UAE",
        "United Arab Emirates": "UAE",
        "Switzerland": "Switzerland",
        "China": "China",
    }

    return aliases.get(name, name)


def parse_stock(data):
    stock = {
        f"{country}|{item}": None
        for country, items in WATCH.items()
        for item in items
    }

    countries = data.get("countries", data)

    if isinstance(countries, dict):
        country_entries = []

        for country_name, country_data in countries.items():
            if isinstance(country_data, dict):
                entry = dict(country_data)
                entry.setdefault("name", country_name)
                country_entries.append(entry)

    elif isinstance(countries, list):
        country_entries = countries

    else:
        raise RuntimeError(
            "Unexpected Torn Intel response structure"
        )

    for country_data in country_entries:
        if not isinstance(country_data, dict):
            continue

        country_name = (
            country_data.get("name")
            or country_data.get("country")
            or country_data.get("country_name")
            or country_data.get("code")
        )

        if not country_name:
            continue

        country_name = normalize_country(
            str(country_name)
        )

        if country_name not in WATCH:
            continue

        items = (
            country_data.get("items")
            or country_data.get("stock")
            or []
        )

        if isinstance(items, dict):
            item_entries = []

            for item_name, item_data in items.items():
                if isinstance(item_data, dict):
                    entry = dict(item_data)
                    entry.setdefault("name", item_name)
                else:
                    entry = {
                        "name": item_name,
                        "quantity": item_data,
                    }

                item_entries.append(entry)

        elif isinstance(items, list):
            item_entries = items

        else:
            continue

        for item_data in item_entries:
            if not isinstance(item_data, dict):
                continue

            item_name = (
                item_data.get("name")
                or item_data.get("item_name")
                or item_data.get("item")
            )

            if item_name not in WATCH[country_name]:
                continue

            quantity = item_data.get("quantity")

            if quantity is None:
                quantity = item_data.get("stock")

            if quantity is None:
                quantity = item_data.get("amount")

            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                continue

            key = f"{country_name}|{item_name}"
            stock[key] = quantity

    return stock


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


def main():
    DATA_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    data = fetch_data()
    stock = parse_stock(data)

    found = sum(
        value is not None
        for value in stock.values()
    )

    print(
        f"Parsed {found}/{len(stock)} watched items"
    )

    # Fail closed:
    # Ikke overskriv gammel state hvis API-formatet
    # endrer seg eller parsing feiler.
    if found < 8:
        print(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            )[:5000]
        )

        raise RuntimeError(
            f"Only parsed {found}/{len(stock)} watched "
            "items. Previous state preserved."
        )

    previous = load_previous()

    snapshot = {
        "timestamp": timestamp,
        "source": URL,
        "travel_slots": 28,
        "stock": stock,
    }

    # Lagre alle observasjoner
    for key, value in stock.items():
        country, item = key.split("|", 1)

        append_csv(
            HISTORY,
            [
                "timestamp",
                "country",
                "item",
                "stock",
            ],
            {
                "timestamp": timestamp,
                "country": country,
                "item": item,
                "stock": (
                    ""
                    if value is None
                    else value
                ),
            },
        )

    # Registrer bare ekte state-overganger
    if previous:
        old_stock = previous.get("stock", {})

        for key, new_value in stock.items():
            old_value = old_stock.get(key)

            if (
                old_value is None
                or new_value is None
            ):
                continue

            transition = None

            # positiv -> 0
            if (
                old_value > 0
                and new_value == 0
            ):
                transition = "stockout"

            # 0 -> positiv
            elif (
                old_value == 0
                and new_value > 0
            ):
                transition = "restock"

            # positiv -> høyere positiv teller
            # fortsatt IKKE som restock.

            if transition:
                country, item = key.split("|", 1)

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
                    ],
                    {
                        "lower_bound":
                            previous.get("timestamp"),
                        "upper_bound":
                            timestamp,
                        "country":
                            country,
                        "item":
                            item,
                        "from_stock":
                            old_value,
                        "to_stock":
                            new_value,
                        "type":
                            transition,
                    },
                )

    LATEST.write_text(
        json.dumps(
            snapshot,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"Collected {found}/{len(stock)} "
        f"watched items at {timestamp}"
    )


if __name__ == "__main__":
    main()
