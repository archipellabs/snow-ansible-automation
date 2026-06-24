# lib/ — shared stdlib clients

Zero-dependency Python modules the bootstrap scripts layer on, so provisioning stays transparent and
dependency-free:

- **`poc.py`** — transport (`.env` parsing, HTTP/JSON, TLS context, basic auth).
- **`servicenow.py`** — the `Snow` client + Business-Rule builder.
- **`aap.py`** — the controller / EDA client with job polling.
- **`runtime.py`** — the AAP / AWX runtime selection (the `--runtime` seam).

📖 **How they fit the whole → [docs/03-architecture.md](../docs/03-architecture.md)**.
