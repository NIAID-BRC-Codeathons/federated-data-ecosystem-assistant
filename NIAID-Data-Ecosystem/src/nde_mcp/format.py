"""Shaping NDE API responses into compact, provenance-carrying results.

NDE records are schema.org JSON-LD: deeply nested, heavy on `DefinedTerm`
wrappers, and padded with embedding vectors. Passing them through verbatim
burns context and buries the fields that matter. Everything here flattens the
common wrappers and keeps the response small enough to reason over.
"""

from __future__ import annotations

from typing import Any, Sequence

from .client import PORTAL_RESOURCE_URL
from .query import DETAIL_EXCLUDE_PREFIXES

# Truncation bounds for summary hits. Detail lookups get a longer allowance.
SUMMARY_DESCRIPTION_CHARS = 400
DETAIL_DESCRIPTION_CHARS = 4000
MAX_LIST_ITEMS = 10


def names_of(value: Any, limit: int = MAX_LIST_ITEMS) -> list[str]:
    """Pull display strings out of a schema.org value.

    Handles the shapes NDE actually uses: a bare string, a list of strings, a
    `DefinedTerm`-style dict with `name`/`displayName`, or a list of those.
    """
    out: list[str] = []

    def add(item: Any) -> None:
        if item is None or len(out) >= limit:
            return
        if isinstance(item, str):
            if item.strip():
                out.append(item.strip())
        elif isinstance(item, dict):
            for key in ("name", "displayName", "title", "identifier", "url", "term"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    out.append(val.strip())
                    return
                if isinstance(val, list) and val:
                    add(val[0])
                    return
        elif isinstance(item, (list, tuple)):
            for sub in item:
                add(sub)

    if isinstance(value, (list, tuple)):
        for item in value:
            add(item)
    else:
        add(value)

    # Preserve order while dropping duplicates.
    seen: set[str] = set()
    deduped = []
    for name in out:
        low = name.lower()
        if low not in seen:
            seen.add(low)
            deduped.append(name)
    return deduped[:limit]


def first_name(value: Any) -> str | None:
    names = names_of(value, limit=1)
    return names[0] if names else None


def truncate(text: Any, limit: int) -> str | None:
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def download_urls(distribution: Any, limit: int = 5) -> list[str]:
    """Extract download URLs from a `distribution` block.

    `DataDownload` entries carry the URL in `contentUrl` and have no `name`, so
    the generic `names_of` helper would miss them entirely. Download links are
    among the most actionable fields in a discovery result, so they get their
    own extractor.
    """
    entries = distribution if isinstance(distribution, list) else [distribution]
    urls: list[str] = []
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            urls.append(entry.strip())
        elif isinstance(entry, dict):
            for key in ("contentUrl", "url", "downloadUrl"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    urls.append(value.strip())
                    break
        if len(urls) >= limit:
            break
    return dedupe_preserving_order(urls)[:limit]


def _repository(hit: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten `includedInDataCatalog`, which may be a dict or a list."""
    catalog = hit.get("includedInDataCatalog")
    if isinstance(catalog, list):
        catalog = catalog[0] if catalog else None
    if not isinstance(catalog, dict):
        name = first_name(hit.get("includedInDataCatalog"))
        return {"name": name} if name else None
    out = {
        "name": catalog.get("name"),
        "url": catalog.get("url"),
        "record_url": catalog.get("archivedAt"),
    }
    return {k: v for k, v in out.items() if v}


def summarize_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Reduce one search hit to the fields useful for triage and routing."""
    record_id = hit.get("_id")
    out: dict[str, Any] = {
        "id": record_id,
        "type": hit.get("@type"),
        "name": hit.get("name") if isinstance(hit.get("name"), str) else first_name(hit.get("name")),
        "description": truncate(hit.get("description"), SUMMARY_DESCRIPTION_CHARS),
    }

    score = hit.get("_score")
    if isinstance(score, (int, float)):
        out["score"] = round(float(score), 4)

    repo = _repository(hit)
    if repo:
        out["repository"] = repo

    # Provenance: where a human or a follow-up tool call can go to verify.
    links = {
        "source_url": hit.get("url"),
        "portal_url": PORTAL_RESOURCE_URL.format(_id=record_id) if record_id else None,
        "doi": hit.get("doi"),
    }
    downloads = download_urls(hit.get("distribution"))
    if downloads:
        links["downloads"] = downloads
    out["links"] = {k: v for k, v in links.items() if v}

    dates = {
        "date": hit.get("date"),
        "published": hit.get("datePublished"),
        "modified": hit.get("dateModified"),
    }
    dates = {k: v for k, v in dates.items() if v}
    if dates:
        out["dates"] = dates

    # Domain annotations, flattened out of their DefinedTerm wrappers.
    annotations = {
        "pathogens": names_of(hit.get("infectiousAgent")),
        "species": names_of(hit.get("species")),
        "health_conditions": names_of(hit.get("healthCondition")),
        "measurement_techniques": names_of(hit.get("measurementTechnique")),
        "topics": names_of(hit.get("topicCategory")),
        "keywords": names_of(hit.get("keywords")),
        "variables_measured": names_of(hit.get("variableMeasured"), limit=8),
        "authors": names_of(hit.get("author"), limit=5),
        "funders": names_of(hit.get("funding"), limit=5),
    }
    out.update({k: v for k, v in annotations.items() if v})

    # ComputationalTool-specific fields.
    software = {
        "programming_languages": names_of(hit.get("programmingLanguage")),
        "application_categories": names_of(hit.get("applicationCategory")),
        "operating_systems": names_of(hit.get("operatingSystem")),
        "software_versions": names_of(hit.get("softwareVersion"), limit=3),
        "code_repository": first_name(hit.get("codeRepository")),
    }
    out.update({k: v for k, v in software.items() if v})

    access = {
        "conditions_of_access": hit.get("conditionsOfAccess"),
        "license": first_name(hit.get("license")),
    }
    out.update({k: v for k, v in access.items() if v})

    finding = measured_finding(hit)
    if finding:
        out["finding"] = finding

    return out


def measured_finding(hit: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the quantitative result out of an observation-style record.

    `Inference` records (Expression Atlas differential expression, ~10.4M on
    the staging deployment) put their actual result in `value` / `unitText`
    with significance in `marginOfError` and the gene in `observationAbout`.
    Those are the point of the record, so they must survive summarization
    rather than being dropped as unrecognized schema.org cruft.
    """
    value = hit.get("value")
    subject = first_name(hit.get("observationAbout"))
    if value is None and not subject:
        return None

    out: dict[str, Any] = {}
    if subject:
        out["about"] = subject
        identifier = (
            hit["observationAbout"].get("identifier")
            if isinstance(hit.get("observationAbout"), dict)
            else None
        )
        if isinstance(identifier, str) and identifier != subject:
            out["about_id"] = identifier
    if value is not None:
        out["value"] = value
    for key, field in (("unit", "unitText"), ("comparison", "measurementQualifier")):
        if isinstance(hit.get(field), str) and hit[field].strip():
            out[key] = hit[field].strip()
    for key, field in (
        ("measured_property", "measuredProperty"),
        ("observation_type", "observationType"),
    ):
        name = first_name(hit.get(field))
        if name:
            out[key] = name

    # marginOfError carries the significance statistic, e.g. adjusted p-value.
    margin = hit.get("marginOfError")
    if isinstance(margin, dict) and margin.get("value") is not None:
        out["margin_of_error"] = {
            "name": margin.get("name"),
            "value": margin.get("value"),
        }
        out["margin_of_error"] = {k: v for k, v in out["margin_of_error"].items() if v is not None}

    source_study = hit.get("subjectOf")
    if isinstance(source_study, dict):
        identifier = source_study.get("identifier")
        if isinstance(identifier, list):
            identifier = identifier[0] if identifier else None
        if isinstance(identifier, str):
            out["from_study"] = identifier

    return out or None


def clean_detail(hit: dict[str, Any], *, description_chars: int = DETAIL_DESCRIPTION_CHARS) -> dict[str, Any]:
    """Return a near-complete record with only the machine-only bulk removed."""
    out: dict[str, Any] = {}
    for key, value in hit.items():
        if any(key.startswith(prefix) for prefix in DETAIL_EXCLUDE_PREFIXES):
            continue
        if key == "description":
            out[key] = truncate(value, description_chars) or value
        else:
            out[key] = value
    record_id = hit.get("_id")
    if record_id:
        out["_portal_url"] = PORTAL_RESOURCE_URL.format(_id=record_id)
    return out


def format_search_response(
    payload: dict[str, Any],
    *,
    query: str,
    size: int,
    offset: int,
    endpoint_url: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap hits with the counts and query echo an agent needs to keep going."""
    hits = payload.get("hits") or []
    total = payload.get("total", 0)

    result: dict[str, Any] = {
        "total": total,
        "returned": len(hits),
        "offset": offset,
        "query": query,
    }
    if endpoint_url:
        result["api_call"] = endpoint_url
    if extra:
        result.update(extra)

    result["results"] = [summarize_hit(h) for h in hits]

    if isinstance(total, int) and offset + len(hits) < total:
        next_offset = offset + len(hits)
        if next_offset < 10_000:
            result["next_offset"] = next_offset
        else:
            result["paging_note"] = (
                "Reached the API's 10,000-record result window. "
                "Narrow the query with filters to reach the remaining records."
            )

    if isinstance(total, int) and total == 0:
        result["hint"] = (
            "No matches. Try fewer filters, a broader free-text term, or "
            "semantic_search for natural-language phrasing. Use nde_list_repositories "
            "or nde_facet_counts to see which values actually exist for a field."
        )

    facets = payload.get("facets")
    if facets:
        result["facets"] = format_facets(facets)

    return result


def format_facets(facets: dict[str, Any]) -> dict[str, Any]:
    """Flatten the facet block into `{field: [{value, count}, ...]}`."""
    out: dict[str, Any] = {}
    for field, body in facets.items():
        if not isinstance(body, dict):
            continue
        terms = [
            {"value": t.get("term"), "count": t.get("count")}
            for t in body.get("terms", [])
            if isinstance(t, dict)
        ]
        entry: dict[str, Any] = {"values": terms}
        for key, label in (("other", "other_count"), ("missing", "missing_count"), ("total", "total_with_field")):
            if body.get(key) is not None:
                entry[label] = body[key]
        out[field] = entry
    return out


def describe_sources(metadata: dict[str, Any], *, name_filter: str | None = None) -> list[dict[str, Any]]:
    """Turn `/metadata` into a flat list of federated repositories."""
    src = metadata.get("src") or {}
    rows: list[dict[str, Any]] = []

    for key, body in src.items():
        if not isinstance(body, dict):
            continue
        info = body.get("sourceInfo") or {}
        name = info.get("name") or key

        # `stats` maps sub-source -> record count; sum for the repository total.
        stats = body.get("stats") or {}
        count = sum(v for v in stats.values() if isinstance(v, int))

        row = {
            "source_key": key,
            "name": name,
            "record_count": count,
            "description": truncate(info.get("description"), 300),
            "url": info.get("url"),
            "identifier": info.get("identifier"),
            "version": body.get("version"),
            "last_updated": body.get("upload_date") or body.get("download_date"),
        }
        row = {k: v for k, v in row.items() if v not in (None, "", 0) or k == "record_count"}

        if name_filter:
            haystack = " ".join(str(v) for v in (key, name, info.get("description") or "")).lower()
            if name_filter.lower() not in haystack:
                continue
        rows.append(row)

    rows.sort(key=lambda r: r.get("record_count") or 0, reverse=True)
    return rows


def format_field_list(fields: dict[str, Any], *, limit: int = 300) -> dict[str, Any]:
    """Condense `/metadata/fields` into name + type pairs."""
    rows = []
    for name, body in sorted(fields.items()):
        ftype = body.get("type") if isinstance(body, dict) else None
        row: dict[str, Any] = {"field": name}
        if ftype:
            row["type"] = ftype
        if isinstance(body, dict) and body.get("searched_by_default"):
            row["searched_by_default"] = True
        rows.append(row)

    out: dict[str, Any] = {"total_fields": len(rows), "returned": min(len(rows), limit)}
    if len(rows) > limit:
        out["note"] = (
            f"Showing the first {limit} of {len(rows)} fields. "
            "Pass a `prefix` or `search` argument to narrow the list."
        )
    out["fields"] = rows[:limit]
    return out


def dedupe_preserving_order(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
