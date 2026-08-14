#!/usr/bin/env python3
"""Download first-front unit icons and write a source manifest.

The simulator does not call the wiki at runtime. Run this script deliberately
when refreshing assets, then review the generated manifest and licensing notes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/logistic_battle_skill_v2.json"
OUT = ROOT / "frontend/public/assets/units"
BASE = "https://wiki.mbxmas.com/zh/units/"
FIRST_FRONT_IDS = {
    10, 9, 28, 30, 15, 2, 31, 7, 8, 13, 20, 21, 24,
    12, 5, 6, 25, 16, 14, 26, 19, 22, 18,
    3, 4, 1, 23, 27, 11, 17, 2002, 29,
}


class OverviewParser(HTMLParser):
    """Extract unit-card links and their square icon URLs from the index page."""

    def __init__(self) -> None:
        super().__init__()
        self.anchor_href: str | None = None
        self.cards: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value for key, value in attrs}
        if tag == "a":
            self.anchor_href = values.get("href")
        elif tag == "img" and values.get("alt") and values.get("src") and self.anchor_href:
            self.cards.append({"name_cn": values["alt"] or "", "src": values["src"] or "", "href": self.anchor_href})

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.anchor_href = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--workers", type=int, default=8, help="parallel icon downloads")
    args = parser.parse_args()
    metadata = json.loads(CATALOG.read_text(encoding="utf-8"))
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    headers = {"User-Agent": "mechabellum-winrate-simulator/0.1"}
    index_error = "unit icon not found on overview page"
    try:
        index_html = urlopen(Request(BASE, headers=headers), timeout=20).read().decode("utf-8", "ignore")
        parser = OverviewParser()
        parser.feed(index_html)
        cards = {card["name_cn"]: card for card in parser.cards}
    except Exception as exc:
        cards = {}
        index_error = str(exc)

    items = [item for item in metadata["unit_axis"] if item.get("unit_id") in FIRST_FRONT_IDS]

    def download(item: dict[str, object]) -> dict[str, object]:
        uid = item["unit_id"]
        card = cards.get(item["name_cn"])
        try:
            if not card:
                raise RuntimeError(index_error)
            source_page = urljoin(BASE, card["href"])
            # The overview page emits `/Unit Squares/...`; encode the space as
            # `%20` so the request matches the site's direct static asset URL.
            source_image = urljoin(BASE, quote(card["src"], safe="/%"))
            data = urlopen(Request(source_image, headers=headers), timeout=20).read()
            target = output / f"{uid}.png"
            target.write_bytes(data)
            return {"unit_id": uid, "name_cn": item["name_cn"], "source_page": source_page, "source_image": source_image, "file": str(target.relative_to(ROOT))}
        except Exception as exc:  # a missing icon should not stop the rest of the catalog
            return {"unit_id": uid, "name_cn": item["name_cn"], "source_page": None, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        manifest = list(executor.map(download, items))
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest)} entries to {output / 'manifest.json'}")


if __name__ == "__main__":
    main()
