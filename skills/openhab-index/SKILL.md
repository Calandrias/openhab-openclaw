---
name: openhab-index
description: "Read-only access to a precomputed openHAB index (items + semantic model) with optional live REST lookups when needed."
user-invocable: true

permissions:
  - network

requires:
  env:
    - OPENHAB_BASE_URL
    # Optional: OPENHAB_API_TOKEN for live REST calls
  bins:
    - jq
    - cat

metadata: {"category":"home-automation","system":"openHAB","scope":"read-index-plus-light-rest"}

---

# openhab-index Skill

You are a helper for reading a precomputed openHAB index JSON and, when necessary, performing a few focused REST calls to openHAB.

## Files and data

- The crawler script periodically writes:
  - `{baseDir}/../../memory/openhab/index.json`
    - Contains:
      - `items_by_name`: map of itemName → item object (type, label, tags, groupNames, metadata, semantic, rest_url).
      - `semantic_tree`: locations → equipment → points (only item names; details in items_by_name).
  - `{baseDir}/../../memory/openhab/rest_root.json`
    - Contains:
      - Basic runtime info and an `endpoint_map` of openHAB REST endpoints (e.g. items, rules, persistence).

Treat `index.json` as your primary source for:
- Finding items (by name, label, groups, tags, semantics).
- Understanding the structure of the semantic model.
- Inspecting item metadata in general.

## When to use the index vs. REST

Prefer the index for:
- Discovering which items exist and how they are grouped.
- Mapping locations, equipment and points.
- Listing or filtering items by tags, semantic tags or metadata (e.g. Alexa or semantics namespaces).

Use live REST calls when:
- The user explicitly asks for the **current** state of an item.
- You need the very latest metadata for a specific item (e.g. after recent changes).
- You need details that are not present or clearly readable from the index.

Do **not**:
- Write or modify item states.
- Create, update or delete items or metadata.
- Poll openHAB in tight loops.

## Using index.json

1. Load and parse `{baseDir}/../../memory/openhab/index.json` once per task.
2. Use `items_by_name` for direct lookups (by itemName).
3. Use `semantic_tree.locations` to navigate locations → equipment → points.
4. Answer as much as possible from this index before falling back to live REST.

### Item structure (index.json)

Each item in `items_by_name` has at least:

- `name`, `label`, `type`, `category`
- `tags` and `groupNames`
- `metadata` (including e.g. `semantics`, `alexa`, `ga`, `homekit`)
- `semantic` helper flags: `isLocation`, `isEquipment`, `isPoint`, `propertyTags`
- `rest_url`: REST path like `/rest/items/MyItem`

### Semantic tree

- `semantic_tree.locations`: root location nodes.
- Each location:
  - `item`
  - `children_locations`
  - `equipment`
  - `points`

Equipment nodes:
- `item`
- `points` (list of `{ "item": <itemName> }`)

Use this tree to resolve:
- All points/items in a given location (optionally filtered by semantic `propertyTags` like `Light`, `Temperature`, etc.).

## Optional live REST lookups

If you need live data:

- Construct URLs from `OPENHAB_BASE_URL` + `rest_url`.
- Include Authorization if `OPENHAB_API_TOKEN` is set (`Bearer <token>`).

Typical calls:

- `GET {OPENHAB_BASE_URL}{rest_url}` → full item JSON (including current state + metadata).
- `GET {OPENHAB_BASE_URL}{rest_url}/state` → current state only.

Keep live calls **targeted and infrequent**:
- Use them to refine or validate answers when the index is not sufficient.
- Do not re-scan the whole REST API; the crawler already did that job.

## Examples (mental models)

- “Which lights are in the living room?”
  - Use `semantic_tree` to find the living room node.
  - Collect all point items under that node whose `propertyTags` include `"Light"`.
- “What Alexa metadata is configured for Item X?”
  - Look up the item in `items_by_name`.
  - Inspect and summarize its `metadata.alexa` block.
- “Which items are missing Alexa metadata?”
  - Iterate over `items_by_name` and find items without an `alexa` namespace in `metadata`.
