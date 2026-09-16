"""Pathogen Data Network (PDN) MCP Server

A remote MCP server exposing tools over Pathogen Data Network resources:
    - LAPIS - pathogen sequences and metadata from Pathoplexus,
      GenSpectrum Loculus, and CoV-Spectrum open

Run over HTTP:  python mcp_servers/pdn.py
Run over stdio: python mcp_servers/pdn.py --stdio
"""

import difflib
import time

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP(
    name="PDN MCP",
    dependencies=["mcp", "requests"],
    instructions=(
        "Query Pathogen Data Network (PDN) databases of pathogen sequences "
        "through LAPIS"
    ),
    port=8001,
    streamable_http_path="/mcp-pdn",
)


# LAPIS  (Pathoplexus, GenSpectrum Loculus, CoV-Spectrum open)
#
# LAPIS serves one URL per organism, and each organism declares its own
# metadata fields. The tools read those fields at runtime, so no field name
# is hard-coded here.

LAPIS_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "federated-data-ecosystem-assistant "
        "(+https://github.com/NIAID-BRC-Codeathons/federated-data-ecosystem-assistant)"
    ),
}

# An organism schema changes rarely, so refetch it after an hour.
LAPIS_SCHEMA_TTL_SECONDS = 3600

# No LAPIS endpoint lists the organisms. These names come from each
# database's API documentation page, checked on 2026-09-16.
LAPIS_DATABASES = {
    "pathoplexus": {
        "name": "Pathoplexus",
        "description": "Human viral pathogens",
        "organism_url": "https://lapis.pathoplexus.org/{organism}",
        "organisms": [
            "andv", "cchf", "dengue", "ebola-bdbv", "ebola-sudan", "ebola-zaire",
            "hmpv", "marburg", "measles", "mpox", "rsv-a", "rsv-b", "west-nile",
            "yellow-fever", "zika",
        ],
    },
    "genspectrum": {
        "name": "GenSpectrum Loculus",
        "description": "Influenza lineages and dengue serotypes",
        "organism_url": "https://api.loculus.genspectrum.org/{organism}",
        "organisms": [
            "b-victoria", "denv1", "denv2", "denv3", "denv4", "h1n1pdm", "h3n2",
            "h5n1", "influenza-a", "influenza-b",
        ],
    },
    "cov-spectrum": {
        "name": "CoV-Spectrum open",
        "description": "Open SARS-CoV-2 data from Nextstrain",
        # A single dataset, so the URL takes no organism name.
        "organism_url": "https://lapis.cov-spectrum.org/open/v2",
        "organisms": ["sars-cov-2"],
    },
}

_lapis_schema_cache: dict[str, tuple[float, dict]] = {}


def _lapis_organism(organism: str) -> tuple[dict, str]:
    """Return the database and the LAPIS URL for an organism name."""
    for database in LAPIS_DATABASES.values():
        if organism in database["organisms"]:
            return database, database["organism_url"].format(organism=organism)
    known = [o for d in LAPIS_DATABASES.values() for o in d["organisms"]]
    close = difflib.get_close_matches(organism, known, n=3)
    hint = f" Close matches: {', '.join(close)}." if close else ""
    raise ToolError(
        f"Unknown organism '{organism}'.{hint} "
        "Call lapis_list_organisms for the full list."
    )


def _lapis_get(url: str) -> dict:
    resp = requests.get(url, headers=LAPIS_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _lapis_schema(organism: str) -> dict:
    """Fetch the fields, segments, and genes for an organism, with a cache."""
    cached = _lapis_schema_cache.get(organism)
    if cached and time.monotonic() - cached[0] < LAPIS_SCHEMA_TTL_SECONDS:
        return cached[1]
    _, url = _lapis_organism(organism)
    config = _lapis_get(f"{url}/sample/databaseConfig")["schema"]
    reference = _lapis_get(f"{url}/sample/referenceGenome")
    schema = {
        "instance_name": config["instanceName"],
        "primary_key": config["primaryKey"],
        "fields": {m["name"]: m["type"] for m in config["metadata"]},
        "phylo_tree_fields": [
            m["name"] for m in config["metadata"] if m.get("isPhyloTreeField")
        ],
        # The reference genome carries full sequences. Keep only the names.
        "segments": [s["name"] for s in reference["nucleotideSequences"]],
        "genes": [g["name"] for g in reference["genes"]],
    }
    _lapis_schema_cache[organism] = (time.monotonic(), schema)
    return schema


@mcp.tool()
def lapis_list_organisms() -> dict:
    """List the pathogens that the LAPIS tools can query, grouped by database.

    Every other LAPIS tool takes one of these organism names. Pathoplexus
    holds human viral pathogens. GenSpectrum Loculus holds influenza lineages
    and dengue serotypes. CoV-Spectrum open holds open SARS-CoV-2 data.

    Returns:
        Each database with its description, organism names, and LAPIS URL
        pattern.

    Example questions:
        "Which pathogens does the Pathogen Data Network have sequences for?"
        "Can I query H5N1 influenza sequences?"
    """
    return {
        "databases": [
            {
                "name": d["name"],
                "description": d["description"],
                "organisms": d["organisms"],
                "url_pattern": d["organism_url"],
            }
            for d in LAPIS_DATABASES.values()
        ],
        "next_step": (
            "Call lapis_describe_organism before you filter. Each organism "
            "declares its own fields."
        ),
    }


@mcp.tool()
def lapis_describe_organism(organism: str, field_search: str = "") -> dict:
    """Describe the metadata fields, segments, and genes for one organism.

    Call this before you filter or group. Each organism declares its own
    fields, and a field name that works for one organism can fail for another.
    For example, Pathoplexus stores the collection date in
    sampleCollectionDate, but CoV-Spectrum stores it in date.

    Without field_search, the response lists every field of the organism,
    often more than 100. Pass field_search to list only the fields you need.

    Args:
        organism: An organism name from lapis_list_organisms, e.g. "mpox",
            "h5n1", "sars-cov-2".
        field_search: Case-insensitive text that a field name must contain,
            e.g. "country", "date", "host". Leave empty to list every field.
            A search also leaves out the segments and genes.

    Returns:
        The matching fields with their types, the phylogenetic tree fields,
        and notes on versions and data use terms. Without field_search, the
        segments and genes too.

    Example questions:
        "Which metadata fields can I filter mpox sequences on?"
        "Which date fields does the H5N1 database have?"
        "Which genes can I query for measles?"
    """
    database, url = _lapis_organism(organism)
    schema = _lapis_schema(organism)
    needle = field_search.lower()
    fields = [
        {"name": name, "type": field_type}
        for name, field_type in schema["fields"].items()
        if needle in name.lower()
    ]
    notes = []
    if "versionStatus" in schema["fields"]:
        notes.append(
            "The database keeps every revision of a sequence, and a revocation "
            "stays as its own record. Filter on versionStatus='LATEST_VERSION' "
            "and isRevocation=false to count each sequence once."
        )
    if "dataUseTerms" in schema["fields"]:
        notes.append(
            "Some records carry RESTRICTED data use terms. Read dataUseTerms, "
            "dataUseTermsRestrictedUntil, and dataUseTermsUrl before you reuse "
            "a record."
        )
    description = {
        "organism": organism,
        "database": database["name"],
        "lapis_url": url,
        "instance_name": schema["instance_name"],
        "primary_key": schema["primary_key"],
    }
    # A field search is a field lookup, so it skips the segments and genes.
    if not field_search:
        description["segments"] = schema["segments"]
        description["genes"] = schema["genes"]
    description.update({
        "phylo_tree_fields": schema["phylo_tree_fields"],
        "field_search": field_search,
        "fields_matched": len(fields),
        "fields_total": len(schema["fields"]),
        "fields": fields,
        "notes": notes,
    })
    return description


if __name__ == "__main__":
    import sys
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print("PDN MCP Server starting on http://localhost:8001/mcp-pdn ...")
        mcp.run(transport="streamable-http")
