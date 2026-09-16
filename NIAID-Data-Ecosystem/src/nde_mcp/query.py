"""Lucene query construction for the NDE API.

The NDE `/query` endpoint takes an Elasticsearch `query_string` in `q`. Tools in
this server accept structured filter arguments and assemble the query string
here, so the model never has to hand-write Lucene (though `nde_raw_query` still
lets it, as an escape hatch).
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

# Characters with special meaning in the Lucene query_string syntax. We quote
# filter values rather than escaping each one, but a literal quote or backslash
# inside the value still has to be escaped.
_QUOTE_ESCAPE = str.maketrans({'"': '\\"', "\\": "\\\\"})

# Filter argument name -> the indexed field it maps to. Keeping this table
# explicit (rather than passing user field names straight through) means a typo
# becomes a clear error instead of a silently empty result set.
FILTER_FIELDS: dict[str, str] = {
    "record_type": "@type",
    "repository": "includedInDataCatalog.name",
    "pathogen": "infectiousAgent.name",
    "host_species": "species.name",
    "health_condition": "healthCondition.name",
    "measurement_technique": "measurementTechnique.name",
    "topic": "topicCategory.name",
    "keyword": "keywords",
    "author": "author.name",
    "funder": "funding.funder.name",
    "license": "license",
    "conditions_of_access": "conditionsOfAccess",
    "programming_language": "programmingLanguage",
    "operating_system": "operatingSystem",
    "doi": "doi",
    "identifier": "identifier",
    "nct_id": "nctid",
}

RECORD_TYPES = (
    "Dataset",
    "ComputationalTool",
    "ResourceCatalog",
    "DataCollection",
    "Sample",
    # `Inference` currently exists only on the staging deployment, where it
    # carries ~10.4M gene-level differential-expression findings from
    # Expression Atlas. Harmless to offer against production, where it simply
    # matches nothing.
    "Inference",
)

# Annotation vocabularies are not shared across record types: `Sample` records
# keep the submitter's raw term ("RNA-seq"), while `Dataset` records carry a
# curated ontology term ("rna-seq assay", OBI). So an intuitive filter pair like
# @type:Dataset + measurementTechnique.name:"RNA-seq" matches nothing even
# though 2.3M datasets do have an RNA-seq technique. These hints are surfaced
# when a query returns zero results because of such a field.
VOCABULARY_HINTS: dict[str, str] = {
    "measurementTechnique.name": (
        "Assay vocabularies differ by record type: Sample records use raw submitter "
        'terms ("RNA-seq"), Dataset records use curated OBI terms ("rna-seq assay", '
        '"whole genome sequencing assay", "sequencing assay"). Run nde_facet_counts '
        'on "measurementTechnique.name" with the same record_type to see the terms '
        "that actually exist for that type."
    ),
    "infectiousAgent.name": (
        "Pathogen names are curated scientific names "
        '("Influenza A virus", "Mycobacterium tuberculosis"). Run nde_facet_counts '
        'on "infectiousAgent.name" to find the exact spelling.'
    ),
    "healthCondition.name": (
        'Condition names are curated ontology terms. Run nde_facet_counts on '
        '"healthCondition.name" to find the exact spelling.'
    ),
    "topicCategory.name": (
        "Topics come from the EDAM ontology and apply mainly to ComputationalTool "
        'records. Run nde_facet_counts on "topicCategory.name" to list them.'
    ),
    "includedInDataCatalog.name": (
        "Repository names must match exactly. Call nde_list_repositories for the "
        "canonical list."
    ),
}


def vocabulary_hint(field: str) -> str | None:
    """Return guidance for a field whose values are a controlled vocabulary."""
    return VOCABULARY_HINTS.get(field)

# Fields worth returning for a compact hit. Full records run to hundreds of
# keys (embeddings, nested sample collections, full citation lists), which
# swamps a model's context for no benefit during discovery.
SUMMARY_SOURCE: tuple[str, ...] = (
    "_id",
    "@type",
    "name",
    "description",
    "url",
    "doi",
    "identifier",
    "date",
    "datePublished",
    "dateModified",
    "includedInDataCatalog.name",
    "includedInDataCatalog.url",
    "includedInDataCatalog.archivedAt",
    "author.name",
    "infectiousAgent.name",
    "infectiousAgent.displayName",
    "species.name",
    "healthCondition.name",
    "measurementTechnique.name",
    "topicCategory.name",
    "keywords",
    "conditionsOfAccess",
    "license",
    "distribution.contentUrl",
    "sourceOrganization.name",
    "funding.funder.name",
    "programmingLanguage",
    "applicationCategory",
    "operatingSystem",
    "softwareVersion",
    "codeRepository",
    "isBasedOn.identifier",
    "variableMeasured.name",
    # Inference records (Expression Atlas differential expression): the
    # measured value IS the finding, so it has to survive summarization.
    "value",
    "unitText",
    "marginOfError.name",
    "marginOfError.value",
    "observationAbout.name",
    "observationAbout.identifier",
    "measuredProperty.name",
    "observationType.name",
    "measurementQualifier",
    "subjectOf.identifier",
)

# Bulky or machine-only fields to strip from an otherwise-full record.
DETAIL_EXCLUDE_PREFIXES: tuple[str, ...] = (
    "ibmGraniteEmbedding",
    "_ignored",
    "_score",
    "all",
)


def escape_value(value: str) -> str:
    """Escape a value for use inside a Lucene double-quoted phrase."""
    return value.translate(_QUOTE_ESCAPE)


def _term_clause(field: str, value: str) -> str:
    """Render one `field:value` clause.

    Range expressions (`[2024-01-01 TO *]`) and explicit wildcards are passed
    through unquoted; everything else is quoted so that spaces, hyphens, and
    colons in ontology terms don't fragment into separate tokens.
    """
    value = value.strip()
    if (value.startswith("[") and value.endswith("]")) or (
        value.startswith("{") and value.endswith("}")
    ):
        return f"{field}:{value}"
    if value == "*":
        return f"{field}:*"
    return f'{field}:"{escape_value(value)}"'


def _or_group(field: str, values: Sequence[str]) -> str:
    """Combine multiple accepted values for one field into an OR group."""
    clauses = [_term_clause(field, v) for v in values if str(v).strip()]
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


def _as_list(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if str(v).strip()]


def date_range_clause(
    field: str, date_from: str | None, date_to: str | None
) -> str | None:
    """Build an inclusive range clause, open-ended on either side."""
    if not date_from and not date_to:
        return None
    lower = date_from.strip() if date_from else "*"
    upper = date_to.strip() if date_to else "*"
    return f"{field}:[{lower} TO {upper}]"


def build_clauses(
    text: str | None = None,
    *,
    filters: dict[str, str | Sequence[str] | None] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "date",
    extra_clauses: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """Build the query's clauses as ``(label, lucene)`` pairs.

    Keeping the clauses separate -- rather than only their joined form -- lets
    the zero-result diagnostic re-test them individually to report which filter
    eliminated every match.
    """
    clauses: list[tuple[str, str]] = []

    text = (text or "").strip()
    if text and text != "__all__":
        # A multi-term free-text phrase gets parenthesized so that appending
        # ``AND field:"x"`` doesn't rebind only the last term.
        clauses.append(("query", f"({text})" if _needs_grouping(text) else text))

    for name, value in (filters or {}).items():
        values = _as_list(value)
        if not values:
            continue
        field = FILTER_FIELDS.get(name, name)
        group = _or_group(field, values)
        if group:
            clauses.append((name, group))

    rng = date_range_clause(date_field, date_from, date_to)
    if rng:
        clauses.append(("date range", rng))

    clauses.extend(("filter", c) for c in extra_clauses if c)
    return clauses


def join_clauses(clauses: Sequence[tuple[str, str]]) -> str:
    """Join clause pairs into a single `q` string, or `__all__` if empty."""
    if not clauses:
        return "__all__"
    return " AND ".join(lucene for _, lucene in clauses)


def build_query(
    text: str | None = None,
    *,
    filters: dict[str, str | Sequence[str] | None] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "date",
    extra_clauses: Iterable[str] = (),
) -> str:
    """Assemble the full `q` string from free text plus structured filters.

    Returns ``__all__`` when nothing was specified, which the API treats as
    match-all -- useful for pure faceting calls.
    """
    return join_clauses(
        build_clauses(
            text,
            filters=filters,
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
            extra_clauses=extra_clauses,
        )
    )


def _needs_grouping(text: str) -> bool:
    """True if free text has multiple tokens and isn't already grouped."""
    if " " not in text:
        return False
    if text.startswith("(") and text.endswith(")"):
        return False
    return True


def clamp_paging(size: int, offset: int) -> tuple[int, int]:
    """Clamp paging arguments to the API's accepted window.

    The API rejects ``size > 1000`` and ``from + size > 10000``.
    """
    size = max(0, min(int(size), 1000))
    offset = max(0, int(offset))
    if offset >= 10_000:
        offset = 10_000 - 1
    if offset + size > 10_000:
        size = max(0, 10_000 - offset)
    return size, offset


def normalize_sort(sort: str | None) -> str | None:
    """Map friendly sort names onto API sort expressions."""
    if not sort:
        return None
    aliases = {
        "relevance": None,
        "newest": "-date",
        "oldest": "date",
        "recently_updated": "-dateModified",
        "name": "name.keyword",
    }
    if sort in aliases:
        return aliases[sort]
    return sort


def build_params(
    query: str,
    *,
    size: int,
    offset: int,
    source: Sequence[str] | None,
    sort: str | None = None,
    facets: Sequence[str] | None = None,
    facet_size: int | None = None,
    use_ai_search: bool = False,
) -> dict[str, Any]:
    """Assemble the GET /query parameter dict."""
    size, offset = clamp_paging(size, offset)
    params: dict[str, Any] = {"q": query, "size": size}
    if offset:
        params["from"] = offset
    if source:
        params["_source"] = list(source)
    if sort:
        params["sort"] = sort
    if facets:
        params["facets"] = list(facets)
    if facet_size:
        params["facet_size"] = facet_size
    if use_ai_search:
        params["use_ai_search"] = True
    return params
