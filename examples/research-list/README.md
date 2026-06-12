# Example — research a list of companies

[`entities.json`](entities.json) is a ready-to-run argument object for the
`entity-research` workflow: three companies, three fields each, with verification on.

## Run it (from Claude Code)

```js
Workflow({ name: 'entity-research', args: <contents of entities.json> })
```

The workflow fans out one bounded research agent per company, each running the cost
waterfall (free search + free-first fetch, escalating only on failure), then a verify pass
that re-opens every `source_url`. It returns:

```json
{
  "fields": ["hq_city", "employee_count", "ceo", "source_url", "verified", "note",
             "internal_status", "internal_ref", "internal_note"],
  "rows": [
    {
      "entity": "Stripe", "hq_city": "South San Francisco, CA",
      "employee_count": "~8000", "ceo": "Patrick Collison",
      "source_url": "https://stripe.com/about", "verified": true,
      "note": "", "internal_status": "net-new", "internal_ref": "", "internal_note": "no known-companies DB configured"
    }
  ],
  "summary": { "entities_in": 3, "entities_returned": 3, "verified": 9, "unverified": 0, "paused": false }
}
```

## The shape, not a promise

The exact values depend on what's live on the web when you run it and which rungs your keys
unlock. The point is the **shape**: one source-verified row per finding, each fact carrying the
URL that proves it, with an `internal_status` column that reads `net-new` until you populate a
`known_companies` table. With `RESEARCH_DATABASE_URL` set, the second run of any of these
companies' pages/queries is a cache hit at zero spend.
