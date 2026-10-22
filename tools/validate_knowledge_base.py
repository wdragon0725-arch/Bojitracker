from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "knowledge-base"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


manifest = load(KB / "manifest.json")
catalog = load(KB / "catalog.json")

assert manifest["schema_version"] == "1.0.0"
assert manifest["coverage"]["worksheets"] == 31
assert len(catalog["sheets"]) == 31
assert len(catalog["entities"]) == 9

cell_count = 0
formula_count = 0
sheet_names = {sheet["title"] for sheet in catalog["sheets"]}
for sheet in catalog["sheets"]:
    raw_path = KB / sheet["raw_file"]
    assert raw_path.exists(), raw_path
    raw = load(raw_path)
    assert raw["title"] == sheet["title"]
    assert len(raw["cells"]) == sheet["nonempty_cells"]
    cell_count += len(raw["cells"])
    formula_count += sum(1 for cell in raw["cells"] if cell["formula"])

assert cell_count == manifest["coverage"]["nonempty_cells"]

for entity in catalog["entities"]:
    assert (KB / entity["file"]).exists(), entity["file"]
    load(KB / entity["file"])

foods = load(KB / "entities" / "foods.json")
assert len(foods["carbohydrate_foods"]) >= 70
assert len(foods["protein_foods"]) >= 15
assert len(foods["application_foods"]) == 21
for food in foods["application_foods"]:
    assert food["source"]["sheet"] in sheet_names
    assert food["source"]["cell"]

meal_plans = load(KB / "entities" / "meal-plans.json")
assert len(meal_plans["plans"]) == 15

training = load(KB / "entities" / "training-plans.json")
assert len(training["plans"]) == 4

one_rm = load(KB / "entities" / "one-rep-max.json")
assert len(one_rm["formulas"]) == 9

chunk_count = sum(1 for line in (KB / "search" / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
assert chunk_count == manifest["coverage"]["search_chunks"]

bundle = (KB / "browser-bundle.js").read_text(encoding="utf-8")
assert bundle.startswith("window.BOJI_KB = ")
assert '"馒头 / 花卷"' in bundle

print(json.dumps({
    "status": "ok",
    "worksheets": len(catalog["sheets"]),
    "cells": cell_count,
    "formulas": formula_count,
    "entities": len(catalog["entities"]),
    "search_chunks": chunk_count,
    "mapped_images": manifest["coverage"]["mapped_images"],
    "raw_media_files": manifest["coverage"]["raw_media_files"],
}, ensure_ascii=False, indent=2))
