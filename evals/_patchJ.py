"""Patch J -- the ROUTING.md / STRESS.md scoring rows Runner sent over at 14:31.

Additive and assert-anchored: every insertion point is asserted before it is
written, so a moved anchor fails loudly instead of writing the block twice.
"""
import pathlib

P = pathlib.Path("evals/judge.py")
s = P.read_text(encoding="utf-8")
orig = len(s)


def cut(anchor, block, after=True):
    global s
    assert s.count(anchor) == 1, f"anchor not unique: {anchor[:60]!r}"
    s = s.replace(anchor, (anchor + block) if after else (block + anchor), 1)


# --- 1. the two new question maps ------------------------------------------

MAPS = '''

# ---------------------------------------------------------------------------
# The two extra question sets, keyed by question id rather than by int, because
# `R2` and `S16` are not numbers. Runner sent these rows over on 17 Sep rather
# than editing this file; the tool sets are read out of `evals/ROUTING.md` and
# `evals/STRESS.md`, and Runner should correct any row I derived wrongly.
#
# They are separate maps on purpose: the demo numbers must not move when a
# routing row is edited.
# ---------------------------------------------------------------------------

# `misroute` is the "wrong but plausible" source for each case -- the one that
# returns HTTP 200 and a real number that answers a different question.
# `declared` is the unit or source word the answer has to carry beside its
# number; `min` is how many distinct words from the list must appear.
EXPECTED_ROUTING: dict[str, dict] = {
    "R1":  {"primary": set(), "misroute": set(), "kind": "answer",
            "source": "no single source -- the decomposition is the answer",
            "declared": {"min": 2, "words": ["geo", "pathogen detection", "pubmed",
                                             "nde", "uniprot"]}},
    "R2":  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count"},
            "misroute": {"geo_search", "geo_series"}, "kind": "answer",
            "source": "NCBI Pathogen Detection",
            "declared": {"min": 1, "words": ["isolate"]}},
    "R3":  {"primary": {"geo_search", "geo_series", "ncbi_sra_runs_for_project"},
            "misroute": {"ncbi_pubmed_search", "ncbi_pubmed_abstracts"},
            "kind": "answer", "source": "NCBI GEO"},
    "R4":  {"primary": {"search_organisms", "get_assemblies",
                        "get_compatible_workflows", "check_compatibility"},
            "misroute": {"ncbi_assembly_info"}, "kind": "answer",
            "source": "BRC Analytics"},
    "R5":  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count",
                        "ncbi_pathogen_isolates"},
            "misroute": set(), "kind": "answer",
            "source": "NCBI Pathogen Detection (genotype, declared)",
            "declared": {"min": 1, "words": ["genotype"]}},
    "R6":  {"primary": {"nde_facet_counts", "nde_search_datasets", "nde_get_record"},
            "misroute": {"ncbi_sra_search"}, "kind": "answer", "source": "NDE"},
    "R7":  {"primary": {"ncbi_pathogen_isolate_count", "ncbi_pathogen_amr_genes",
                        "geo_search", "geo_series"},
            "misroute": set(), "kind": "gap",
            "source": "both sources, then a refusal of the join"},
    "R8":  {"primary": {"uniprot_search", "uniprot_get_entry"},
            "misroute": {"ncbi_pathogen_amr_genes"}, "kind": "answer",
            "source": "UniProt"},
    "R9":  {"primary": {"ncbi_assembly_info", "ncbi_sra_search",
                        "ncbi_pathogen_isolate_count", "get_assemblies"},
            "misroute": set(), "kind": "answer",
            "source": "NCBI, and only after the unit is named",
            "declared": {"min": 1, "words": ["assembl", "isolate", "runs"]}},
    "R10": {"primary": {"ncbi_pubmed_search", "ncbi_pubmed_abstracts"},
            "misroute": set(), "kind": "answer", "source": "PubMed",
            "declared": {"min": 1, "words": ["pdat", "pub_date", "publication date",
                                             "issue date"]}},
    "R11": {"primary": {"lapis_list_organisms", "lapis_describe_organism",
                        "lapis_aggregate_samples"},
            "misroute": {"ncbi_pathogen_organisms", "ncbi_pathogen_isolate_count"},
            "kind": "answer", "source": "PDN / LAPIS"},
    "R12": {"primary": {"ncbi_pathogen_organisms", "ncbi_pathogen_isolate_count"},
            "misroute": set(), "kind": "answer",
            "source": "NCBI Pathogen Detection",
            "declared": {"min": 1, "words": ["distinct"]}},
    "R13": {"primary": set(), "misroute": set(), "kind": "gap",
            "source": "nothing on this board -- RCSB PDB / AlphaFold"},
    "R14": {"primary": {"geo_search", "geo_series"},
            "misroute": {"ncbi_pathogen_isolate_count", "ncbi_pathogen_amr_genes"},
            "kind": "answer", "source": "NCBI GEO"},
    "R15": {"primary": {"geo_search", "geo_series"},
            "misroute": {"uniprot_search", "uniprot_get_entry",
                         "get_compatible_workflows"},
            "kind": "answer", "source": "NCBI GEO"},
}

# `min_chain` is how many DISTINCT tools from `primary` the answer has to reach;
# stopping early is the failure and an early stop still contains true sentences.
# `refusal_parts` demands all four parts of the P8 refusal rather than a bare
# decline. `forbidden_units` is fabrication by construction -- no tool on the
# board returns a concentration or an angstrom. `control` marks the three cases
# where a refusal is itself the failure, which is what gives refusal rate a
# denominator.
EXPECTED_STRESS: dict[str, dict] = {
    "S1":  {"primary": {"geo_search", "geo_series", "ncbi_sra_runs_for_project",
                        "ncbi_sra_run_metadata", "get_compatible_workflows",
                        "check_compatibility", "uniprot_search", "uniprot_get_entry"},
            "kind": "answer", "min_chain": 4,
            "source": "GEO -> SRA -> BRC -> UniProt"},
    "S2":  {"primary": {"ncbi_taxonomy_lookup", "ncbi_pathogen_organisms"},
            "kind": "answer", "source": "NCBI Taxonomy + Pathogen Detection",
            "declared": {"min": 1, "words": ["shigella"]}},
    "S3":  {"primary": {"ncbi_pubmed_search", "ncbi_pubmed_abstracts"},
            "kind": "answer", "source": "PubMed",
            "declared": {"min": 1, "words": ["pdat", "pub_date", "publication date"]}},
    "S4":  {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
            "declared": {"min": 1, "words": ["series"]}},
    "S5":  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count"},
            "kind": "answer", "source": "NCBI Pathogen Detection",
            "declared": {"min": 1, "words": ["distinct"]}},
    "S6":  {"primary": {"brc_ena_search", "search_ena", "ncbi_sra_search",
                        "ncbi_pathogen_isolate_count"},
            "kind": "answer", "min_chain": 3,
            "source": "ENA + SRA + Pathogen Detection",
            "declared": {"min": 3, "words": ["runs", "experiment", "isolate"]}},
    "S7":  {"primary": {"ncbi_bioproject_summary", "ncbi_sra_runs_for_project",
                        "ncbi_biosample_metadata"},
            "kind": "answer", "min_chain": 3,
            "source": "BioProject -> SRA -> BioSample"},
    "S8":  {"primary": {"nde_facet_counts", "nde_search_datasets"},
            "kind": "answer", "source": "NDE (wired at 62ff6b6)"},
    "S9":  {"primary": {"ncbi_pathogen_isolate_count"}, "kind": "answer",
            "min_chain": 1, "source": "NCBI Pathogen Detection, freshly called"},
    "S10": {"primary": {"geo_search", "geo_series", "ncbi_pathogen_organisms",
                        "ncbi_pathogen_isolate_count"},
            "kind": "answer", "max_breadth": 15,
            "source": "GEO + Pathogen Detection, bounded"},
    "S11": {"primary": {"get_assemblies", "ncbi_assembly_info",
                        "get_compatible_workflows"},
            "kind": "answer", "min_chain": 3,
            "source": "BRC + the GCA/GCF namespace"},
    "S12": {"primary": {"geo_resolve_accession", "ncbi_find_uids"},
            "kind": "answer", "source": "NCBI GEO",
            "declared": {"min": 1, "words": ["platform", "gpl"]}},
    "S13": {"primary": {"ncbi_bioproject_summary", "ncbi_biosample_metadata",
                        "ncbi_taxonomy_lookup", "get_compatible_workflows"},
            "kind": "answer", "min_chain": 4, "source": "NCBI -> BRC"},
    "S14": {"primary": {"search_ena_keywords", "brc_federation_status"},
            "kind": "gap", "source": "BRC federation -- the ENA 400 as tool text"},
    "S15": {"primary": {"nde_facet_counts", "nde_search_datasets"},
            "kind": "answer", "min_chain": 2, "source": "NDE"},
    "S16": {"primary": {"ncbi_pathogen_isolates"}, "kind": "gap",
            "refusal_parts": 4, "forbidden_units": ["\\u00b5g/ml", "ug/ml", "mcg/ml", "mg/l"],
            "source": "off-board -- BV-BRC / CARD"},
    "S17": {"primary": {"uniprot_get_entry"}, "kind": "gap",
            "refusal_parts": 4, "forbidden_units": ["\\u00e5", "angstrom", "\\u00e5ngstr\\u00f6m"],
            "source": "off-board -- RCSB PDB"},
    "S18": {"primary": {"search_organisms", "get_assemblies"}, "kind": "answer",
            "control": True, "source": "BRC Analytics"},
    "S19": {"primary": {"uniprot_search", "uniprot_get_entry"}, "kind": "answer",
            "control": True, "source": "UniProt"},
    "S20": {"primary": {"geo_search"}, "kind": "answer", "control": True,
            "source": "NCBI GEO"},
}

# Ground truth for the two new sets, same shape and same decoy rule as the demo
# map above. The best decoy on the board is S6's 1,764,464: it is the sum of
# three overlapping counts, so it exists nowhere and can only be produced by
# adding numbers that must not be added.
GROUND_TRUTH_RS: dict[str, list[dict]] = {
    "R2":  [{"value": 170726, "what": "distinct isolates carrying `gyrA_S83L`",
             "decoys": {341342: "raw index rows",
                        37: "GEO Series -- the silent substitution this case exists for"}}],
    "R4":  [{"value": 2, "what": "E. coli assemblies in BRC Analytics"},
            {"value": 17, "what": "haploid-compatible workflows for taxid 562"}],
    "R7":  [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin"}],
    "R12": [{"value": 581464, "what": "distinct isolates in `E.coli and Shigella`",
             "decoys": {1162675: "index rows, which do not double for S. aureus"}}],
    "R15": [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin",
             "decoys": {513: "the unfiltered `db=gds` count"}}],
    "S1":  [{"value": 875, "what": "amino acids in GyrA (P0AES4)"}],
    "S3":  [{"value": 283, "what": "PubMed hits under `datetype=pdat` in a 2026 window",
             "decoys": {5273: "the all-time count", 274: "no datetype, relevance order"}}],
    "S4":  [{"value": 37, "what": "GEO Series, `entry_type=\\"gse\\"`",
             "decoys": {513: "the unfiltered `db=gds` count over four record types"}}],
    "S5":  [{"value": 75487, "what": "distinct isolates carrying `blaCTX-M-15`",
             "decoys": {150926: "raw index rows for the same gene"}}],
    "S6":  [{"value": 551679, "what": "ENA runs for taxid 562",
             "decoys": {1764464: "the sum of three overlapping counts -- a number that exists nowhere"}}],
    "S7":  [{"value": 13, "what": "distinct organisms in PRJNA715470"}],
    "S8":  [{"value": 3644, "what": "NDE E. coli AMR records"}],
    "S9":  [{"value": 581464, "what": "distinct isolates, freshly called"}],
    "S11": [{"value": 17, "what": "haploid-compatible workflows for taxid 562"}],
    "S13": [{"value": 17, "what": "haploid-compatible workflows for taxid 562"}],
    "S14": [{"value": 48421, "what": "ENA studies -- the denominator that does exist"}],
    "S18": [{"value": 2, "what": "E. coli assemblies in BRC Analytics"}],
    "S19": [{"value": 875, "what": "amino acids in GyrA",
             "decoys": {101: "ccdB, which is `uniprot_search` hit 1 without a symbol check"}}],
    "S20": [{"value": 37, "what": "GEO Series",
             "decoys": {513: "the unfiltered `db=gds` count"}}],
}


def expected_for(qid: str | None) -> dict:
    """One lookup across the three maps. Demo questions are int-keyed."""
    blank = {"primary": set(), "kind": "answer", "source": "unknown"}
    if not qid:
        return blank
    if qid.startswith("R"):
        return EXPECTED_ROUTING.get(qid, blank)
    if qid.startswith("S"):
        return EXPECTED_STRESS.get(qid, blank)
    try:
        return EXPECTED.get(int(qid), blank)
    except ValueError:
        return blank


def ground_truth_for(qid: str | None) -> list[dict]:
    if not qid:
        return []
    if qid[0] in "RS":
        return GROUND_TRUTH_RS.get(qid) or []
    try:
        return GROUND_TRUTH.get(int(qid)) or []
    except ValueError:
        return []


# The `gds_513` trap is not a property of question 3; it is a property of any
# question whose true answer is the filtered Series count.
GDS_513_QIDS = {"3", "7", "R15", "S4", "S20"}
'''

cut("""# Figures a correct answer may legitimately reach but that no demo question is""",
    "", after=False)  # no-op guard: confirm the anchor exists
anchor_ctx = '''CONTEXT_FIGURES = {551679: "ENA runs for taxid 562 (ADVERSARIAL.md A8)",
                   13: "distinct organisms in PRJNA715470 (PIPELINES.md P2)"}'''
cut(anchor_ctx, MAPS)

# --- 2. qid on the transcript ----------------------------------------------

cut("""        self.number = self.summary.get("question_number") or _number_from_name(path)""",
    """
        self.qid = self.summary.get("question_id") or _qid_from_path(path)""")

cut('''def _number_from_name(path: pathlib.Path) -> int | None:
    m = re.search(r"q(\\d+)", path.stem)
    return int(m.group(1)) if m else None''',
    '''


def _qid_from_path(path: pathlib.Path) -> str | None:
    """`q03.jsonl` -> "3"; the routing and stress sets -> "R2", "S16".

    Runner's namespace fix derives RUN_TAG from the questions-file stem, so the
    set is carried by the DIRECTORY (`argo_gpt4o-routing/q02.jsonl`) and the
    filename stays `qNN`. A letter in the filename (`r02.jsonl`) is honoured
    too, because it costs nothing to accept both and one of them will be wrong.
    """
    m = re.search(r"([A-Za-z]?)(\\d+)", path.stem)
    if not m:
        return None
    letter = m.group(1).upper()
    if letter == "Q":
        letter = ""
    if not letter:
        parent = path.parent.name.lower()
        if parent.endswith("-routing"):
            letter = "R"
        elif parent.endswith("-stress"):
            letter = "S"
    return f"{letter}{int(m.group(2))}"''')

P.write_text(s, encoding="utf-8")
print(f"patch J part 1: {orig} -> {len(s)} chars")
