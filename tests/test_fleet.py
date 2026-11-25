from pathlib import Path
import json
from collections import defaultdict

def test_vehicle_source_consistent():
    root = Path(__file__).resolve().parents[1]
    vehicles = json.loads((root / "data" / "veiculos_exportados.json").read_text(encoding="utf-8"))["vehicles"]
    assert len(vehicles) == 72
    assert len({v["id"] for v in vehicles}) == 72
    grouped = defaultdict(set)
    for vehicle in vehicles:
        plate = "".join(c for c in vehicle["place_number"].upper() if c.isalnum())
        grouped[plate].add(vehicle["vehicle_type"])
    assert len(grouped) == 66
    assert all(len(types) == 1 for types in grouped.values())
