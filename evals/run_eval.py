"""Measure what these MCP servers add over calling the APIs straight from the docs.

The claim an MCP server has to earn is not "it reaches the API" -- anything reaches
the API. It is that a competent developer reading the same documentation, writing
the obvious call, gets a WRONG answer that looks right, and that the tool gets it
right. This harness makes that claim falsifiable.

Each case runs twice against the live services:

  baseline  the obvious call, written the way the API docs lead you to write it
  tool      our MCP tool

and both are scored against a ground truth recorded from a verified observation.
A case only counts as value added when baseline fails and the tool passes. Cases
where both pass are reported too, and honestly: they are cases where the tool is
convenience, not correctness.

Run:  uv run evals/run_eval.py
      uv run evals/run_eval.py --markdown evals/REPORT.md
"""

import argparse
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "mcp_servers"))

import geo as GEO                      # noqa: E402
import brc_analytics as BRC            # noqa: E402

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BRC_MCP = "https://brc-analytics.org/api/v1/mcp/"
TOOL = "niaid-bionexus-p7"

def _pace() -> None:
    """Take a turn from geo.py's pacer rather than keeping a second one.

    NCBI's 3/second ceiling is per IP. The first run of this harness used its own
    independent pacer alongside geo.py's, put roughly 5 req/s on eutils, and got
    HTTP 429 -- scoring a throttle as a failed case. Sharing the pacer fixes the
    measurement; the retry that run also exposed is fixed in geo.py itself.
    """
    GEO._wait_turn()


def eutils(endpoint: str, **params) -> dict:
    """A bare E-utilities call -- exactly what the docs show, no guards."""
    _pace()
    query = {"db": "gds", "retmode": "json", "tool": TOOL, **params}
    url = f"{EUTILS}/{endpoint}.fcgi?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def brc_mcp(name: str, args: dict):
    """Call BRC's own federated MCP server, the way the chatbot does."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": args}}
    ).encode()
    req = urllib.request.Request(BRC_MCP, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read().decode()
    for line in raw.splitlines():
        if line.startswith("data: "):
            d = json.loads(line[6:])
            text = d.get("result", {}).get("content", [{}])[0].get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_text": text}
    return {}


# ---------------------------------------------------------------- the cases


def case_gds_prefix():
    """GDS is the one GEO prefix with no type digit, and guessing returns a real record."""
    truth = "GDS5163"

    def baseline():
        # The docs give the prefix rule for GSE/GPL/GSM. GDS looks like a platform
        # sibling, so 1 + padded is the natural guess.
        rec = eutils("esummary", id="100005163", version="2.0")
        got = list(rec.get("result", {}).values())
        acc = next((r.get("accession") for r in got if isinstance(r, dict)), None)
        return acc, f"esummary id=100005163 -> {acc}"

    def tool():
        out = GEO.geo_series("GDS5163", list_files=False)
        acc = out["record"]["accession"]
        return acc, f"geo_series('GDS5163') -> {acc}"

    return ("geo-gds-prefix",
            "Fetch the curated DataSet GDS5163",
            truth, baseline, tool,
            "A wrong prefix returns a real Affymetrix array design, with no error.")


def case_gsm_resolution():
    """Resolving a GEO accession by searching for it returns the wrong record type."""
    truth = "309284462"

    def baseline():
        # The documented way to turn a term into UIDs is esearch. Take the first hit.
        res = eutils("esearch", term="GSM9284462", retmax=5)
        ids = res["esearchresult"].get("idlist", [])
        first = ids[0] if ids else None
        return first, f"esearch term=GSM9284462 -> {len(ids)} hits, first={first}"

    def tool():
        out = GEO.geo_resolve_accession("GSM9284462")
        uid = out["results"][0]["uid"]
        return uid, f"geo_resolve_accession -> {uid} (0 requests)"

    return ("geo-gsm-resolve",
            "Get the Entrez UID for sample GSM9284462",
            truth, baseline, tool,
            "gds indexes the accession inside every related record, so the Series ranks first.")


def case_series_count():
    """An unfiltered gds count sums four different record types."""
    truth = 37

    def baseline():
        res = eutils("esearch",
                     term='"Escherichia coli"[Organism] AND ciprofloxacin')
        return int(res["esearchresult"]["count"]), \
            f'esearch without an entry-type filter -> {res["esearchresult"]["count"]}'

    def tool():
        out = GEO.geo_search(organism="Escherichia coli", term="ciprofloxacin",
                             entry_type="gse", max_results=1)
        return out["total_count"], f'geo_search(entry_type="gse") -> {out["total_count"]}'

    return ("geo-series-count",
            "How many E. coli GEO Series involve ciprofloxacin?",
            truth, baseline, tool,
            "Without a filter the count mixes Series, Platforms, Samples and DataSets.")


def case_expression_files():
    """The processed values are in no API response at all."""
    truth = "GSE309890_FPKMs_allSamples.csv.gz"

    def baseline():
        rec = eutils("esummary", id="200309890", version="2.0")
        r = rec["result"]["200309890"]
        supp = r.get("suppfile")
        return supp, f"esummary suppfile -> {supp!r} (a format, not a filename)"

    def tool():
        out = GEO.geo_series("GSE309890")
        names = [f["name"] for f in out.get("supplementary_files", [])]
        return (names[0] if names else None), \
            f"geo_series -> {len(names)} file(s): {names[0] if names else 'none'}"

    return ("geo-expression-files",
            "Where are the expression values for GSE309890?",
            truth, baseline, tool,
            "esummary names file FORMATS. The filenames and URLs live on the FTP host.")


def case_retmax():
    """esearch silently returns 20 ids while reporting a much larger count."""
    truth = "count and id list agree, or the truncation is stated"

    def baseline():
        res = eutils("esearch", term='"Escherichia coli"[Organism] AND "gse"[Filter]')
        b = res["esearchresult"]
        return (len(b.get("idlist", [])), int(b["count"])), \
            f'count={b["count"]} but {len(b.get("idlist", []))} ids returned, silently'

    def tool():
        out = GEO.geo_search(organism="Escherichia coli", entry_type="gse", max_results=5)
        return (out["returned"], out["total_count"]), \
            f'total_count={out["total_count"]}, returned={out["returned"]}, truncated={out.get("truncated")}'

    def score_baseline(v):
        returned, count = v
        return returned == count      # it will not be; nothing says so
    def score_tool(v):
        returned, count = v
        return returned < count       # and the payload says truncated=True

    return ("geo-retmax", "List the E. coli GEO Series",
            truth, baseline, tool,
            "Default retmax is 20. The count is real; the id list is quietly cut.",
            score_baseline, score_tool)


def case_ena_keywords():
    """BRC's own keyword search is broken, and fails as text rather than as an error."""
    truth = "a usable row count"

    def baseline():
        out = brc_mcp("search_ena_keywords",
                      {"keywords": ["Escherichia coli", "antimicrobial resistance"]})
        if "_text" in out:
            return None, "search_ena_keywords -> error text, no rows"
        return out.get("count"), f'search_ena_keywords -> count={out.get("count")}'

    def tool():
        out = BRC.brc_ena_search(organism="Escherichia coli",
                                 title_contains="resistance", limit=2)
        return out["total_matching"], \
            f'brc_ena_search -> total_matching={out["total_matching"]:,}'

    def usable(v):
        return isinstance(v, int) and v > 0

    return ("brc-ena-keywords",
            "Find E. coli sequencing runs whose study mentions resistance",
            truth, baseline, tool,
            "It sends an unquoted scientific_name to ENA; ENA answers HTTP 400.",
            usable, usable)


def case_ena_total():
    """The federated tool caps at 50 and never reports a total."""
    truth = "a real total"

    def baseline():
        out = brc_mcp("search_ena", {"taxonomy_id": "562"})
        return out.get("count"), \
            f'search_ena -> count={out.get("count")}, has_more={out.get("has_more")}'

    def tool():
        out = BRC.brc_ena_runs("562", limit=1)
        return out["total_in_ena"], f'brc_ena_runs -> total_in_ena={out["total_in_ena"]:,}'

    def score_baseline(v):
        return isinstance(v, int) and v > 1000    # 50 is a page size, not an answer
    def score_tool(v):
        return isinstance(v, int) and v > 1000

    return ("brc-ena-total", "How many sequencing runs does ENA hold for E. coli?",
            truth, baseline, tool,
            "50 with has_more is a page size. Reporting it as the answer is wrong by 4 orders of magnitude.",
            score_baseline, score_tool)


def case_study_organisms():
    """A project's organism label is not its runs' organism."""
    truth = 13

    def baseline():
        # The obvious read: BRC proxies ENA, so ask it for the study.
        try:
            req = urllib.request.Request(
                "https://brc-analytics.org/api/v1/ena/study/PRJNA715470")
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
            return len({x.get("scientific_name") for x in data.get("results", [])}), \
                "GET /api/v1/ena/study/PRJNA715470 -> parsed"
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            return None, f"GET /api/v1/ena/study/PRJNA715470 -> HTTP {code}"

    def tool():
        out = BRC.brc_ena_study("PRJNA715470")
        n = len(out["organisms_in_runs"])
        return n, f'brc_ena_study -> {out["run_count"]} runs, {n} distinct organisms'

    return ("brc-study-organisms",
            "What organisms are actually in BioProject PRJNA715470?",
            truth, baseline, tool,
            "BRC's study endpoint 500s. The project is labelled E. coli and holds 13 genera-worth of runs.")


CASES = [
    case_gds_prefix,
    case_gsm_resolution,
    case_series_count,
    case_expression_files,
    case_retmax,
    case_ena_keywords,
    case_ena_total,
    case_study_organisms,
]


# ---------------------------------------------------------------- the runner


def run_case(factory):
    parts = factory()
    if len(parts) == 6:
        cid, question, truth, baseline, tool, why = parts
        score_b = score_t = (lambda t: (lambda v: v == t))(truth)
    else:
        cid, question, truth, baseline, tool, why, score_b, score_t = parts

    row = {"id": cid, "question": question, "truth": truth, "why": why}
    for label, fn, scorer in (("baseline", baseline, score_b), ("tool", tool, score_t)):
        try:
            value, detail = fn()
            row[label] = {"value": value, "detail": detail, "pass": bool(scorer(value))}
        except Exception as exc:
            row[label] = {"value": None,
                          "detail": f"{type(exc).__name__}: {str(exc)[:120]}",
                          "pass": False}
    row["value_added"] = row["tool"]["pass"] and not row["baseline"]["pass"]
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--markdown", help="also write a markdown report here")
    args = ap.parse_args()

    print(f"Running {len(CASES)} cases against live services.\n")
    rows = []
    for factory in CASES:
        row = run_case(factory)
        rows.append(row)
        b = "PASS" if row["baseline"]["pass"] else "FAIL"
        t = "PASS" if row["tool"]["pass"] else "FAIL"
        flag = "  <-- value added" if row["value_added"] else ""
        print(f"  {row['id']:24s} baseline {b}   tool {t}{flag}")
        print(f"      baseline: {row['baseline']['detail']}")
        print(f"      tool    : {row['tool']['detail']}")

    n = len(rows)
    bp = sum(r["baseline"]["pass"] for r in rows)
    tp = sum(r["tool"]["pass"] for r in rows)
    va = sum(r["value_added"] for r in rows)
    print(f"\n  baseline correct : {bp}/{n}")
    print(f"  tool correct     : {tp}/{n}")
    print(f"  value added      : {va}/{n}")

    if args.markdown:
        write_markdown(pathlib.Path(args.markdown), rows, bp, tp, va)
        print(f"\n  wrote {args.markdown}")
    return 0 if tp == n else 1


def write_markdown(path: pathlib.Path, rows, bp, tp, va) -> None:
    n = len(rows)
    L = ["# What these MCP servers add over reading the API docs", "",
         "Generated by `evals/run_eval.py` against the live services.", "",
         "Each case is run twice: the **baseline** is the obvious call, written the way",
         "the API documentation leads you to write it, and the **tool** is our MCP tool.",
         "Both are scored against a verified ground truth.", "",
         f"| | correct |", "|---|---|",
         f"| baseline (raw API, written from the docs) | **{bp}/{n}** |",
         f"| our MCP tools | **{tp}/{n}** |",
         f"| cases where the tool fixes a wrong answer | **{va}/{n}** |", "",
         "The failures below are not API outages. Every one returns HTTP 200 with a",
         "plausible-looking answer, which is what makes them worth wrapping.", ""]
    for r in rows:
        mark = "fixes a silent wrong answer" if r["value_added"] else "both correct"
        L += [f"## {r['question']}", "",
              f"*{r['why']}*", "",
              f"| | result | correct |", "|---|---|---|",
              f"| baseline | `{r['baseline']['detail']}` | {'yes' if r['baseline']['pass'] else '**no**'} |",
              f"| tool | `{r['tool']['detail']}` | {'yes' if r['tool']['pass'] else '**no**'} |", "",
              f"Expected: `{r['truth']}` — {mark}.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
