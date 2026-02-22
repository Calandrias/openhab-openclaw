# scripts/openhab_crawler.py

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("OPENHAB_BASE_URL")
TOKEN = os.getenv("OPENHAB_API_TOKEN")

OUTPUT_DIR = Path("./memory/openhab")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REST_ROOT_FILE = OUTPUT_DIR / "rest_root.json"
INDEX_FILE = OUTPUT_DIR / "index.json"

def make_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return headers


async def fetch_json(client: httpx.AsyncClient, url: str) -> Any:
    resp = await client.get(url, headers=make_headers(), timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    return resp.text


async def fetch_rest_root() -> Dict[str, Any]:
    """
    Fetch /rest/ root, build an endpoint_map, keep original links,
    and write a compact rest_root.json that OpenClaw can also read.
    """
    if not BASE_URL:
        raise RuntimeError("OPENHAB_BASE_URL is not set")

    rest_url = urljoin(BASE_URL.rstrip("/") + "/", "rest/")
    async with httpx.AsyncClient() as client:
        resp = await client.get(rest_url, headers=make_headers(), timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        root_data = resp.json()

    # Build a simple 1st-level endpoint map: type -> url
    endpoint_map: Dict[str, str] = {}
    for link in root_data.get("links", []):
        ltype = link.get("type")
        url = link.get("url")
        if ltype and url:
            endpoint_map[ltype] = url

    # Keep original links + add endpoint_map and some basic info
    rest_root_enriched: Dict[str, Any] = {
        "version": root_data.get("version"),
        "locale": root_data.get("locale"),
        "measurementSystem": root_data.get("measurementSystem"),
        "timezone": root_data.get("timezone"),
        "runtimeInfo": root_data.get("runtimeInfo"),
        "links": root_data.get("links", []),
        "endpoint_map": endpoint_map,
    }

    with REST_ROOT_FILE.open("w", encoding="utf-8") as f:
        json.dump(rest_root_enriched, f, indent=2, ensure_ascii=False)

    print(f"Wrote REST root -> {REST_ROOT_FILE}")
    
    return rest_root_enriched


def find_link(root: Dict[str, Any], link_type: str) -> Optional[str]:
    """
    Resolve an endpoint URL from rest_root data, preferring endpoint_map,
    falling back to the original links list.
    """
    endpoint_map = root.get("endpoint_map", {})
    if link_type in endpoint_map:
        return endpoint_map[link_type]

    for link in root.get("links", []):
        if link.get("type") == link_type:
            return link.get("url")
    return None


async def fetch_items(root: Dict[str, Any]) -> List[Dict[str, Any]]:
    items_url = find_link(root, "items")
    if not items_url:
        raise RuntimeError("No 'items' link found in /rest/ root")

    async with httpx.AsyncClient() as client:
        # you can add ?recursive=true if you want group members in one go
        resp = await client.get(items_url, headers=make_headers(), timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    return data


def build_items_index(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build:
      - items_by_name: flat index by item name (without state)
      - semantic_tree: simple Location -> Equipment -> Point tree
    """
    items_by_name: Dict[str, Any] = {}
    locations: Dict[str, Dict[str, Any]] = {}
    equipment: Dict[str, Dict[str, Any]] = {}

    for it in items:
        name = it.get("name")
        if not name:
            continue

        tags = it.get("tags") or []
        group_names = it.get("groupNames") or []
        metadata = it.get("metadata") or {}

        # Basic classification based on tags (simplified)
        is_location = any(t.endswith("Location") or t == "Location" for t in tags)
        is_equipment = any(t.endswith("Equipment") or t == "Equipment" for t in tags)
        is_point = any(t.endswith("Point") or t == "Point" for t in tags)

        # Try to identify semantic property (e.g. Light, Temperature)
        property_tags = [t for t in tags if t not in ("Location", "Equipment", "Point")]

        items_by_name[name] = {
            "name": name,
            "label": it.get("label"),
            "type": it.get("type"),
            # state intentionally omitted (volatile)
            "category": it.get("category"),
            "tags": tags,
            "groupNames": group_names,
            "metadata": metadata,  # can be pruned later if too large
            "semantic": {
                "isLocation": is_location,
                "isEquipment": is_equipment,
                "isPoint": is_point,
                "propertyTags": property_tags,
            },
            "rest_url": f"/rest/items/{name}",
        }

        if is_location:
            locations[name] = {"name": name, "tags": tags, "groupNames": group_names}
        if is_equipment:
            equipment[name] = {"name": name, "tags": tags, "groupNames": group_names}

    # Build semantic_tree: locations -> equipment -> points (by item name only)
    semantic_tree: Dict[str, Any] = {"locations": []}
    location_nodes: Dict[str, Dict[str, Any]] = {}

    # First create a node for each location
    for loc_name, loc in locations.items():
        location_nodes[loc_name] = {
            "item": loc_name,
            "children_locations": [],
            "equipment": [],
            "points": [],
        }

    # Assign location hierarchy (location inside location via groupNames)
    for loc_name, loc in locations.items():
        parent_locations = [g for g in loc["groupNames"] if g in location_nodes]
        if not parent_locations:
            # root location
            semantic_tree["locations"].append(location_nodes[loc_name])
        else:
            parent = parent_locations[0]
            location_nodes[parent]["children_locations"].append(location_nodes[loc_name])

    # Map equipment to locations
    for eq_name, eq in equipment.items():
        parent_locations = [g for g in eq["groupNames"] if g in location_nodes]
        if not parent_locations:
            # equipment without location, ignored for tree (still accessible via items_by_name)
            continue
        parent = parent_locations[0]
        location_nodes[parent]["equipment"].append({"item": eq_name, "points": []})

    # Build quick lookup: equipment item -> equipment node in tree
    equipment_nodes: Dict[str, Dict[str, Any]] = {}
    def collect_equipment_nodes(loc_node: Dict[str, Any]) -> None:
        for eq in loc_node["equipment"]:
            equipment_nodes[eq["item"]] = eq
        for child_loc in loc_node["children_locations"]:
            collect_equipment_nodes(child_loc)

    for root_loc in semantic_tree["locations"]:
        collect_equipment_nodes(root_loc)

    # Assign point items to equipment or location
    for name, it in items_by_name.items():
        if not it["semantic"]["isPoint"]:
            continue

        group_names = it["groupNames"]

        # Prefer equipment groups
        parent_eq = next((g for g in group_names if g in equipment_nodes), None)
        if parent_eq:
            equipment_nodes[parent_eq]["points"].append({"item": name})
            continue

        # Fallback to location groups
        parent_loc = next((g for g in group_names if g in location_nodes), None)
        if parent_loc:
            location_nodes[parent_loc]["points"].append({"item": name})
            continue

        # otherwise: point without semantic parent -> omitted from tree but still in items_by_name

    return {
        "items_by_name": items_by_name,
        "semantic_tree": semantic_tree,
    }


async def main() -> None:
    root = await fetch_rest_root()
    items = await fetch_items(root)

    # optional: also save raw items.json for debugging
    raw_items_file = OUTPUT_DIR / "items_raw.json"
    with raw_items_file.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"Wrote raw items -> {raw_items_file}")

    index = build_items_index(items)

    with INDEX_FILE.open("w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(f"Wrote index -> {INDEX_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
