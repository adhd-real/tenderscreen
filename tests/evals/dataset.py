"""Synthetic labelled documents for evaluation tests (no real data)."""

import json
from pathlib import Path

NOT_FOUND = {"status": "not_found"}


def answer(value: object, page: int = 1, quote: str = "synthetic clause") -> dict[str, object]:
    return {"value": value, "source_page": page, "quote": quote}


# The "truth" for two synthetic award notices.
LABELS: dict[str, dict[str, object]] = {
    "award_a": {
        "contract_value_paise": answer("Rs. 25,00,000", page=2),
        "award_date": answer("15/10/2026"),
        "successful_bidder_name": answer("Synthetic Builders Pvt Ltd"),
        "number_of_bids": answer(4),
        "pbg_percentage": NOT_FOUND,
        "contact_details": answer("Executive Engineer, 0000-000000"),
    },
    "award_b": {
        "contract_value_paise": answer("2.5 Cr", page=3),
        "award_date": NOT_FOUND,
        "successful_bidder_name": answer("Example Infra"),
        "number_of_bids": NOT_FOUND,
        "pbg_percentage": answer("5"),
        "contact_details": NOT_FOUND,
    },
}


def write_folder(folder: Path, documents: dict[str, dict[str, object]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for doc_id, fields in documents.items():
        data = {"document": f"{doc_id}.pdf", "fields": fields}
        (folder / f"{doc_id}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return folder
