# Federated NIAID–BRC Data Ecosystem Assistant

**NIAID-BRCs AI Codeathon 2.0** · September 16–18, 2026 · Argonne National Laboratory

Short Description: Allow a user to ask one research question and receive a coordinated plan spanning BV-BRC, BRC Analytics, PDN, the NIAID Data Ecosystem, NCBI resources, and other participating repositories.

Project page: https://niaid-brc-codeathons.github.io/projects/federated-data-ecosystem-assistant/
## Three-Day MVP (proposed)

Register a limited set of MCP-enabled tools from at least three resources. Demonstrate five end-to-end questions, such as finding relevant datasets, retrieving pathogen genomes, identifying available workflows, launching an analysis, and returning a provenance-linked result.

The agent should expose its resource-selection rationale, generated queries, API calls, and intermediate outputs rather than acting as an opaque chatbot.

## Evaluation (proposed)

Ten canonical questions scored for correct resource routing, tool-call success, result relevance, provenance, execution cost, and recovery from failed calls.

## Leads

- Bob Olson
- Panayiotis Smeros

Team assignments are still being finalized. Participants can review their project, and request a reassignment, in the participant spreadsheet circulated by the organizing team.

## Data Sources

TBD

## Demo

1. Clone this repository.

2. Create `.env` and fill in `OPENROUTER_API_KEY` (update accordingly to your LLM authentication, the API key matching whichever `LLM_MODEL` you use in `chatbot.py`).

3. Install dependencies:
`uv sync`

4. To add a resource, create an MCP server for it and name the file `resourcename_mcp.py` (following the pattern in `uniprot_mcp.py`).

5. Run each MCP server (every `*_mcp.py` file) on its own port:
`uv run resourcename_mcp.py --port 8001`

6. Run chatbot:
`uv run chainlit run chatbot.py`

