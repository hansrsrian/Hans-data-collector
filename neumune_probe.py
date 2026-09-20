import csv
from datetime import datetime, timezone

import collector as c

EVENTS = c.DATA_DIR / "neumune_events.csv"
STATE = c.DATA_DIR / "neumune_probe_state.json"
KEY = "Switzerland|Neumune Tablet"
FIELDS = [
    "collection_timestamp",
    "observation_timestamp",
    "stock",
    "event_type",
    "source",
    "source_update",
    "yata_stock",
    "yata_update",
    "prometheus_stock",
    "prometheus_update",
]


def collect_sources():
    providers = {}
    for name, url in {"YATA": c.URL, "Prometheus": c.PROMETHEUS_URL}.items():
        try:
            payload = c.fetch_data(url)
            values, updates = c.parse_stock(payload)
            stock = values.get(KEY)
            update = updates.get("Switzerland")
            if stock is not None and update is not None:
                providers[name] = (stock, update)
        except (OSError, ValueError, RuntimeError) as error:
            print(f"{name} failed: {type(error).__name__}: {error}")
    return providers


def choose_freshest(providers):
    if not providers:
        raise RuntimeError("No valid Neumune observations")
    name, (stock, update) = max(
        providers.items(),
        key=lambda entry: (entry[1][1], entry[0] == "YATA"),
    )
    return name, stock, update


def classify(previous, stock):
    if previous is None:
        return "baseline"
    old = c.nonnegative_integer(previous.get("stock"))
    if old is None:
        return "baseline"
    if old == 0 and stock > 0:
        return "restock"
    if old > 0 and stock == 0:
        return "stockout"
    if old != stock:
        return "quantity_change"
    return None


def provider_value(providers, name, index):
    values = providers.get(name)
    return values[index] if values is not None else None


def main():
    c.DATA_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    providers = collect_sources()
    source, stock, update = choose_freshest(providers)
    previous = c.load_json(STATE, None)
    kind = classify(previous, stock)

    if kind is None:
        print(f"No Neumune change: {stock}")
        return

    row = {
        "collection_timestamp": timestamp,
        "observation_timestamp": c.source_time(update),
        "stock": stock,
        "event_type": kind,
        "source": source,
        "source_update": update,
        "yata_stock": provider_value(providers, "YATA", 0),
        "yata_update": provider_value(providers, "YATA", 1),
        "prometheus_stock": provider_value(providers, "Prometheus", 0),
        "prometheus_update": provider_value(providers, "Prometheus", 1),
    }
    c.append_csv(EVENTS, FIELDS, row)
    c.write_json(STATE, row)
    print(f"Neumune {kind}: {stock} via {source}")


if __name__ == "__main__":
    main()