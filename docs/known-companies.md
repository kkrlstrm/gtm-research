# Known accounts — the internal cross-reference

Web research tells you what's **public** about a company. This optional feature tells you
what's **yours**: is this account already a customer, a prior prospect, or genuinely net-new?
It adds an `internal_status` column to every company you research — the GTM signal generic
enrichment tools structurally can't produce, because it lives in *your* systems, not on the web.

It is a **deterministic database join, not an LLM call.** You populate a `known_companies`
table from your CRM / sending tool; `bin/known-xref.py` reads it. With no table configured,
every company resolves to `net-new` and the research runs unchanged — nothing here is required.

## Why it matters

The status is what connects research to **execution**:

- **Don't prospect what you already serve.** Flag `customer` accounts before they enter outreach.
- **Route by relationship.** Send a `prior-prospect-replied` account to the rep who owns it,
  not into a cold sequence.
- **Suppress before you spend.** Drop `do-not-contact` domains before paid enrichment or a
  sequencer push.

## Setup

```bash
# after storage/postgres/schema.sql:
psql "$RESEARCH_DATABASE_URL" -f storage/postgres/known-companies-optional.sql
```

Then populate the table from wherever your account truth lives (CRM export, warehouse sync,
sending-tool suppression list). The engine only reads it:

```sql
INSERT INTO research.known_companies (company_name, domain, status, notes) VALUES
  ('Acme Corp',  'acme.com',   'customer',       'renewed 2026-Q1'),
  ('Globex',     'globex.io',  'prior-prospect', 'replied, no meeting — owned by Dana'),
  ('Initech',    'initech.com','do-not-contact', 'legal hold');
-- domain_normalized is set automatically (lowercased, no scheme/www/path),
-- so 'https://www.acme.com/' and 'acme.com' match the same row.
```

`status` is free text — use whatever vocabulary your team thinks in. A common convention:

| status | meaning |
|---|---|
| `customer` | active account — don't prospect |
| `prior-prospect` | touched before; route by owner |
| `do-not-contact` | legal/opt-out suppression |
| `net-new` | default when no row matches (not stored — returned on a miss) |

## How it appears in output

For company research, the workflow runs the xref once per entity and copies its result verbatim
into every finding:

```json
{
  "entity": "Acme Corp", "ceo": "Jane Roe", "source_url": "https://acme.com/about",
  "verified": true,
  "internal_status": "customer",
  "internal_ref": "Acme Corp",
  "internal_note": "renewed 2026-Q1"
}
```

Call it directly to test:

```bash
python3 bin/known-xref.py "Acme Corp" --domain acme.com --json
```

## Batch lookups

The migration ships a set-returning function for checking many domains at once (e.g. to
suppress a list before enrichment):

```sql
SELECT * FROM research.check_company_domains(ARRAY['acme.com', 'unknown-co.com']);
--  input_domain   |  status   | company_name | notes
-- ----------------+-----------+--------------+----------------
--  acme.com       | customer  | Acme Corp    | renewed 2026-Q1
--  unknown-co.com | net-new   |              |
```

## Relationship to gtm-pipeline

This is the **company-level** analog of gtm-pipeline's `master_contacts` (contact-level
suppression). Populate both from the same CRM and you get suppression at the account *and* the
person level — research that knows your world at both grains. See
[integrating-with-gtm-pipeline.md](integrating-with-gtm-pipeline.md).
