"""Unpacking the formats E-utilities returns.

Two recurring shapes need help:

**CSV inside efetch.** ``rettype=runinfo`` returns a 47-column CSV that appears
nowhere in NCBI's own E-utilities documentation. Its row order does not follow
the order of the ids you asked for, so results must be joined on the ``Run``
column rather than zipped positionally. (esummary's order *does* match, which
makes this easy to get wrong by analogy.)

**XML inside JSON.** Several esummary records carry a blob of XML as the value
of a JSON string field --- ``expxml`` and ``runs`` in SRA, ``sampledata`` in
BioSample, ``meta`` in Assembly. Some of those blobs are a sequence of sibling
elements with no single root, which ``ElementTree`` rejects, so everything gets
wrapped before parsing.
"""

from __future__ import annotations

import csv
import io
from typing import Any
from xml.etree import ElementTree as ET


def parse_xml_fragment(blob: str) -> ET.Element | None:
    """Parse an XML fragment that may have zero, one, or several root elements.

    NCBI embeds these inside JSON string fields. ``expxml`` is a run of sibling
    elements (``<Summary>...</Summary><Submitter .../><Experiment .../>``) with
    no wrapper, which is not well-formed XML on its own. Wrapping is
    unconditional: doing it to a fragment that already has a single root is
    harmless, and testing first just means two ways to fail.
    """
    if not blob or not blob.strip():
        return None
    try:
        return ET.fromstring(f"<root>{blob}</root>")
    except ET.ParseError:
        return None


def xml_to_dict(element: ET.Element | None) -> Any:
    """Collapse an ElementTree node into plain JSON-able Python.

    Attributes are merged in alongside child elements. Repeated child tags
    become a list. Text-only elements with no attributes collapse to a bare
    string, so the common case reads as data rather than as a parse tree.
    """
    if element is None:
        return None

    result: dict[str, Any] = {}
    result.update(element.attrib)

    for child in element:
        value = xml_to_dict(child)
        if value is None or value == {}:
            continue
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value

    text = (element.text or "").strip()
    if text:
        if not result:
            return text
        result["value"] = text

    return result


def parse_runinfo_csv(
    text: str,
    *,
    drop_columns: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Parse an SRA ``rettype=runinfo`` CSV into records.

    Rows whose ``Run`` cell is empty are dropped: NCBI emits blank lines and
    repeats the header when a request spans several internal batches, and both
    arrive as rows that parse cleanly but mean nothing.
    """
    rows: list[dict[str, str]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        run = (raw.get("Run") or "").strip()
        if not run or run == "Run":  # blank line, or a repeated header
            continue
        record = {
            key: (value or "").strip()
            for key, value in raw.items()
            if key and key not in drop_columns
        }
        rows.append(record)
    return rows


def order_runinfo(
    rows: list[dict[str, str]], requested: list[str]
) -> list[dict[str, str]]:
    """Reorder runinfo rows to match the accessions the caller asked for.

    efetch returns runinfo rows in its own order, so positional assumptions
    silently attach one run's metadata to another run's accession. Joining on
    ``Run`` is the only safe option. Rows NCBI returned that were not requested
    (it expands some accessions) are kept, after the requested ones.
    """
    by_run = {row["Run"]: row for row in rows}
    ordered = [by_run.pop(acc) for acc in requested if acc in by_run]
    ordered.extend(by_run.values())
    return ordered


def missing_accessions(
    rows: list[dict[str, str]], requested: list[str]
) -> list[str]:
    """Accessions the caller asked for that NCBI did not return."""
    returned = {row.get("Run") for row in rows}
    return [acc for acc in requested if acc not in returned]


def parse_esummary_records(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pull per-UID records out of an esummary body. Returns ``(records, errors)``.

    ``result`` is a dict keyed by UID plus a ``uids`` list giving the order.
    A UID listed in ``uids`` but absent from ``result`` is skipped rather than
    raising --- an accession passed where a UID belongs produces exactly that.

    The reason this returns two lists is a **fourth** error shape, one that none
    of the top-level checks in ``envelope.check_for_error`` can see. A UID that
    does not exist comes back as a perfectly ordinary-looking record::

        {"uid": "999999999999", "error": "cannot get document summary"}

    HTTP 200, no top-level ``error``, no ``esummaryresult``. Returned as data it
    reads to an agent as a real record with an odd field, so the per-UID errors
    are split out here and surfaced as notes instead.
    """
    result = payload.get("result")
    if not isinstance(result, dict):
        return [], []
    uids = result.get("uids") or []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for uid in uids:
        record = result.get(uid)
        if not isinstance(record, dict):
            continue
        per_record_error = record.get("error")
        if per_record_error:
            errors.append(f"{uid}: {per_record_error}")
        else:
            records.append(record)
    return records, errors


def parse_taxonomy_records(blob: str) -> list[dict[str, Any]]:
    """Flatten an ``efetch db=taxonomy`` TaxaSet into one dict per requested taxon.

    Taxonomy is the one database where efetch is strictly better than esummary:
    the summary record has no lineage and no genetic code at all, so a tool that
    promises either has to come here. Measured ~7.7 KB/taxon, which is why the
    caller caps the batch.

    Two measured traps, both of which produce wrong data rather than an error:

    **``<TaxId>`` is not one per taxon.** Every ancestor inside ``<LineageEx>``
    is itself a full ``<Taxon>`` element with its own ``TaxId``, ``ScientificName``
    and ``Rank``. Three requested taxa carry 49 ``TaxId`` elements between them.
    ``root.iter("Taxon")`` therefore returns the ancestors as if they were
    results --- ``findall("Taxon")``, direct children only, is load-bearing.

    **The common name has two different element names.** Measured: taxid 9606
    uses ``<GenbankCommonName>`` ("human"), taxid 562 uses ``<CommonName>``
    ("E. coli"), and taxid 1280 has neither. Checking only one spelling silently
    drops the name for half the tree.
    """
    # Not parse_xml_fragment: this is a whole efetch document, and it opens with
    # an XML declaration and a DOCTYPE. That helper wraps its input in <root>
    # unconditionally --- correct for the JSON-embedded fragments it exists for,
    # but it puts the declaration mid-document and every taxon silently
    # disappears behind a ParseError.
    if not blob or not blob.strip():
        return []
    try:
        root = ET.fromstring(blob)
    except ET.ParseError:
        return []

    taxa = root.findall("Taxon")

    records: list[dict[str, Any]] = []
    for taxon in taxa:
        other = taxon.find("OtherNames")
        common = None
        if other is not None:
            for spelling in ("GenbankCommonName", "CommonName"):
                found = other.findtext(spelling)
                if found:
                    common = found
                    break
        records.append(
            {
                "taxid": taxon.findtext("TaxId"),
                "scientific_name": taxon.findtext("ScientificName"),
                "common_name": common,
                "rank": taxon.findtext("Rank"),
                "division": taxon.findtext("Division"),
                "parent_taxid": taxon.findtext("ParentTaxId"),
                "genetic_code": taxon.findtext("GeneticCode/GCName"),
                "lineage": taxon.findtext("Lineage"),
            }
        )
    return records


def expand_sra_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Replace an SRA esummary record's XML string fields with parsed structures."""
    expanded = dict(record)
    for field in ("expxml", "runs"):
        blob = expanded.get(field)
        if isinstance(blob, str) and blob.strip():
            parsed = xml_to_dict(parse_xml_fragment(blob))
            if parsed is not None:
                expanded[field] = parsed
    return expanded


def expand_xml_field(record: dict[str, Any], field: str) -> dict[str, Any]:
    """Same, for a single named field (``sampledata`` in BioSample, ``meta`` in Assembly)."""
    expanded = dict(record)
    blob = expanded.get(field)
    if isinstance(blob, str) and blob.strip():
        parsed = xml_to_dict(parse_xml_fragment(blob))
        if parsed is not None:
            expanded[field] = parsed
    return expanded


def parse_linksets(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Flatten an elink JSON body into ``{target_db: [uid, ...]}``.

    Two measured traps:

    * When there are no links, ``linksetdbs`` is **absent**, not an empty list.
      ``payload["linksets"][0]["linksetdbs"]`` raises ``KeyError`` on the most
      ordinary outcome there is.
    * When several source ids are sent, NCBI **merges** their results into one
      linkset with no per-id attribution. The return type is therefore keyed by
      target database only; callers must not claim which input produced which
      output.
    """
    links: dict[str, list[str]] = {}
    for linkset in payload.get("linksets") or []:
        for linksetdb in linkset.get("linksetdbs", []):
            target = linksetdb.get("dbto")
            if not target:
                continue
            links.setdefault(target, []).extend(linksetdb.get("links") or [])
    return links
