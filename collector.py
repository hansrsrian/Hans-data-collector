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
            "User-Agent": "TornForeignStockCollector/3.0",
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


def main():
    DATA_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    data = fetch_data()

    stock, source_updates = parse_stock(data)

    found = sum(
        value is not None
        for value in stock.values()
    )

    missing = [
        key
        for key, value in stock.items()
        if value is None
    ]

    print(
        f"Parsed {found}/{len(stock)} watched items"
    )

    if missing:
        print(
            "Missing targets: "
            + ", ".join(missing)
        )

    # Fail closed:
    # Ikke endre gammel state hvis YATA-formatet
    # eller datakilden feiler kraftig.
    if found < 8:
        raise RuntimeError(
            f"Only parsed {found}/{len(stock)} watched "
            "items. Previous state preserved."
        )

    previous = load_previous()

    snapshot = {
        "timestamp": timestamp,
        "source": "YATA",
        "source_url": URL,
        "travel_slots": 28,
        "source_updates": source_updates,
        "stock": stock,
    }

    # Lagre observasjoner
    for key, value in stock.items():
        country, item = key.split("|", 1)

        append_csv(
            HISTORY,
            [
                "timestamp",
                "country",
                "item",
                "stock",
                "source",
                "source_update",
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
                "source": "YATA",
                "source_update":
                    source_updates.get(country, ""),
            },
        )

    # Finn ekte state-overganger
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

            # positiv -> 0 = stockout
            if old_value > 0 and new_value == 0:
                transition = "stockout"

            # 0 -> positiv = restock
            elif old_value == 0 and new_value > 0:
                transition = "restock"

            # positiv -> høyere positiv
            # teller IKKE som restock

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
                        "source",
                    ],
                    {
                        "lower_bound":
                            previous.get("timestamp"),
                        "upper_bound":
                            timestamp,
                        "country": country,
                        "item": item,
                        "from_stock": old_value,
                        "to_stock": new_value,
                        "type": transition,
                        "source": "YATA",
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
