import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://weav3r.dev/travel-stock"

DATA_DIR = Path("data")
LATEST = DATA_DIR / "latest.json"
HISTORY = DATA_DIR / "history.csv"
TRANSITIONS = DATA_DIR / "transitions.csv"

# Varene vi skal følge
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

ALIASES = {
    "South Africa": ["South Africa", "SA"],
    "Japan": ["Japan"],
    "Canada": ["Canada"],
    "United Kingdom": ["United Kingdom", "UK"],
    "UAE": ["UAE", "United Arab Emirates"],
    "Switzerland": ["Switzerland"],
    "China": ["China"],
}


def fetch_html():
    request = Request(
        URL,
        headers={
            "User-Agent":
                "Mozilla/5.0 TornForeignStockCollector/1.0"
        },
    )

    with urlopen(request, timeout=30) as response:
        return response.read().decode(
            "utf-8",
            errors="replace"
        )


def clean_text(html):
    text = re.sub(
        r"<script[\s\S]*?</script>",
        " ",
        html,
        flags=re.I,
    )

    text = re.sub(
        r"<style[\s\S]*?</style>",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(r"<[^>]+>", " ", text)

    text = (
        text
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
    )

    return re.sub(r"\s+", " ", text)


def parse_stock(text):
    result = {}

    for country, items in WATCH.items():

        country_positions = []

        for alias in ALIASES[country]:
            country_positions.extend(
                m.start()
                for m in re.finditer(
                    re.escape(alias),
                    text,
                    flags=re.I,
                )
            )

        for item in items:

            key = f"{country}|{item}"
            candidates = []

            for match in re.finditer(
                re.escape(item),
                text,
                flags=re.I,
            ):

                prior = [
                    p
                    for p in country_positions
                    if p <= match.start()
                    and match.start() - p < 5000
                ]

                if not prior:
                    continue

                tail = text[
                    match.end():
                    match.end() + 180
                ]

                numbers = re.findall(
                    r"(?<![.$])\b([0-9][0-9,]*)\b",
                    tail,
                )

                if numbers:
                    distance = (
                        match.start() - max(prior)
                    )

                    stock = int(
                        numbers[0].replace(",", "")
                    )

                    candidates.append(
                        (distance, stock)
                    )

            if candidates:
                result[key] = min(candidates)[1]
            else:
                result[key] = None

    return result


def load_previous():

    if not LATEST.exists():
        return None

    try:
        return json.loads(
            LATEST.read_text(
                encoding="utf-8"
            )
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

    html = fetch_html()

    text = clean_text(html)

    stock = parse_stock(text)

    found = sum(
        value is not None
        for value in stock.values()
    )

    # Hvis nettsiden/parsing feiler,
    # skal gammel state beholdes.
    if found < 8:
        raise RuntimeError(
            f"Only parsed {found}/{len(stock)} "
            "watched items. "
            "Previous state preserved."
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

    # Finn ekte state-overganger
    if previous:

        old_stock = previous.get(
            "stock",
            {}
        )

        for key, new_value in stock.items():

            old_value = old_stock.get(key)

            if (
                old_value is None
                or new_value is None
            ):
                continue

            transition = None

            # positiv -> 0 = utsolgt
            if (
                old_value > 0
                and new_value == 0
            ):
                transition = "stockout"

            # 0 -> positiv = restock
            elif (
                old_value == 0
                and new_value > 0
            ):
                transition = "restock"

            # positiv -> høyere positiv
            # teller IKKE som restock

            if transition:

                country, item = key.split(
                    "|",
                    1,
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
                    ],
                    {
                        "lower_bound":
                            previous.get(
                                "timestamp"
                            ),
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
