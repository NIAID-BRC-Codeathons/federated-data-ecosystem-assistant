"""What each Entrez database is for, and how it misbehaves.

Everything here was measured against the live API in September 2026, not read
out of the documentation. Several entries contradict what the E-utilities
manual implies, which is the reason this table exists as code rather than as a
comment: the tools consult it to refuse requests that would succeed
technically and fail the user.

The ``efetch`` column is the important one. It has three values:

  ``"ok"``        efetch works and returns something usefully sized.
  ``"avoid"``     efetch works but the response is enormous or unbounded.
  ``"broken"``    efetch returns HTTP 200 and a response that is not the data
                  you asked for. This is worse than an error --- nothing in the
                  status code or the body's shape says anything went wrong.
"""

from __future__ import annotations

from typing import NamedTuple


class DatabaseInfo(NamedTuple):
    name: str
    description: str
    efetch: str
    efetch_note: str
    tool: str  # the curated tool that should be used for this database


DATABASES: dict[str, DatabaseInfo] = {
    "sra": DatabaseInfo(
        name="sra",
        description=(
            "Sequence Read Archive. Raw sequencing runs and their library, "
            "platform, and sample metadata. Search by organism, sequencing "
            "strategy, platform, or BioProject accession."
        ),
        efetch="ok",
        efetch_note=(
            "rettype=runinfo gives a 47-column CSV at ~479 bytes/record. Full "
            "XML is ~9.3 KB/record and is the only source of SAMPLE_ATTRIBUTES, "
            "STUDY_ABSTRACT, and SRAFiles download URLs."
        ),
        tool="ncbi_sra_search / ncbi_sra_run_metadata",
    ),
    "biosample": DatabaseInfo(
        name="biosample",
        description=(
            "Descriptions of the biological source materials behind sequencing "
            "data: host, isolation source, collection date, geographic location."
        ),
        efetch="ok",
        efetch_note="Full XML is reasonable, but esummary already embeds most of it.",
        tool="ncbi_biosample_metadata",
    ),
    "bioproject": DatabaseInfo(
        name="bioproject",
        description=(
            "Research project records that group related samples and data. "
            "The usual entry point when a paper cites a PRJNA accession."
        ),
        efetch="avoid",
        efetch_note=(
            "Unbounded: the same call returned 20 KB and 840 KB on different "
            "records, with no parameter controlling the size. Use esummary."
        ),
        tool="ncbi_bioproject_summary",
    ),
    "pubmed": DatabaseInfo(
        name="pubmed",
        description="Biomedical literature citations and abstracts.",
        efetch="ok",
        efetch_note=(
            "rettype=abstract is ~1.6 KB/record. Required for abstract text: "
            "the esummary record has no abstract field at all."
        ),
        tool="ncbi_pubmed_search / ncbi_pubmed_abstracts",
    ),
    "taxonomy": DatabaseInfo(
        name="taxonomy",
        description="Organism names, NCBI taxonomy IDs, and lineage.",
        efetch="ok",
        efetch_note=(
            "Required, not optional: the esummary record has no lineage and no "
            "genetic code at all. ~7.7 KB/taxon vs ~343-byte summaries. Note "
            "that <LineageEx> nests a full <Taxon> per ancestor, so a naive "
            "walk returns ancestors as results."
        ),
        tool="ncbi_taxonomy_lookup",
    ),
    "gene": DatabaseInfo(
        name="gene",
        description="Gene records: symbol, aliases, genomic location, summary.",
        efetch="avoid",
        efetch_note=(
            "efetch retmode=xml returned 34.6 MB for a single gene (TP53). It "
            "inlines every annotation track. Use esummary."
        ),
        tool="ncbi_gene_info",
    ),
    "assembly": DatabaseInfo(
        name="assembly",
        description=(
            "Genome assembly records: accession, level, submitter, and the FTP "
            "paths where the sequence files live."
        ),
        efetch="broken",
        efetch_note=(
            "efetch is not implemented for this database, but it does not say "
            "so. It returns HTTP 200 and a 192-byte <IdList> echoing back the "
            "UID you sent. Use esummary."
        ),
        tool="ncbi_assembly_info",
    ),
    "nuccore": DatabaseInfo(
        name="nuccore",
        description="Nucleotide sequences: GenBank, RefSeq, and related records.",
        efetch="ok",
        efetch_note=(
            "FASTA is compact. GenBank flatfile is far larger --- 37 KB for a "
            "2.5 kb mRNA --- because of the feature table."
        ),
        tool="ncbi_sequence_fetch",
    ),
    "protein": DatabaseInfo(
        name="protein",
        description="Protein sequences from GenBank, RefSeq, SwissProt, and PDB.",
        efetch="ok",
        efetch_note="FASTA is compact.",
        tool="ncbi_sequence_fetch",
    ),
    "gds": DatabaseInfo(
        name="gds",
        description=(
            "GEO DataSets. Curated gene expression and other functional "
            "genomics studies."
        ),
        efetch="broken",
        efetch_note=(
            "retmode=xml returns plain text, not XML, despite the parameter. "
            "Use esummary."
        ),
        tool="ncbi_entrez_raw",
    ),
}


# Databases where efetch must not be used, mapped to the tool that works.
EFETCH_UNSAFE = {
    name: info for name, info in DATABASES.items() if info.efetch != "ok"
}


def describe(db: str) -> DatabaseInfo | None:
    return DATABASES.get(db.lower().strip())


def efetch_guidance(db: str) -> str | None:
    """Return a message explaining why efetch is wrong for ``db``, or None."""
    info = EFETCH_UNSAFE.get(db.lower().strip())
    if info is None:
        return None
    verb = "is not implemented" if info.efetch == "broken" else "is unsafe"
    return (
        f"efetch {verb} for db={info.name!r}: {info.efetch_note} "
        f"Use {info.tool} instead."
    )


# Of runinfo's 47 columns, these 12 were empty in all 200 rows of a public
# viral study (PRJNA257197, measured 2026-09-16).
#
# They are not junk. Every one is a dbGaP controlled-access or 1000 Genomes
# field, so they are empty *because* the study is open-access, and they should
# be expected to populate for controlled data --- which this server, having no
# dbGaP credentials, will never see. Stripping is therefore on by default but
# behind a flag, and a tool that ever starts returning dbGaP records must turn
# it off rather than silently discard subject and phenotype metadata.
RUNINFO_OPEN_ACCESS_EMPTY = (
    "AssemblyName",
    "g1k_pop_code",
    "source",
    "g1k_analysis_group",
    "Subject_ID",
    "Sex",
    "Disease",
    "Affection_Status",
    "Analyte_Type",
    "Histological_Type",
    "Body_Site",
    "dbgap_study_accession",
)
