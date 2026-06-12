-- gtm-research — OPTIONAL "do we already know this company?" table (internal xref).
--
-- Apply AFTER schema.sql (it reuses the `research` schema):
--     psql "$RESEARCH_DATABASE_URL" -f storage/postgres/known-companies-optional.sql
--
-- This is the company-level analog of gtm-pipeline's master_contacts. It powers
-- bin/known-xref.py, which adds an `internal_status` column to research output —
-- the GTM signal generic enrichment tools can't produce ("is this a customer / a
-- prior prospect / net-new?"). WITHOUT it, every company resolves to `net-new`.
--
-- How it gets populated is up to you: sync it from your CRM / sending tool / data
-- warehouse. This file only provides the table + the lookup the xref reads.

create schema if not exists research;

create table if not exists research.known_companies (
    id                serial primary key,
    company_name      text,
    domain            text,
    domain_normalized text,                          -- lowercased, no scheme/www/path (set by trigger)
    status            text not null default 'prior-contact',
        -- free-text; a common convention: customer | prior-contact | do-not-contact | net-new
    notes             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    unique (domain_normalized)
);
create index if not exists ix_known_companies_name on research.known_companies (lower(company_name));

-- Normalize a domain the same way bin/known-xref.py does (lower, strip scheme/www/path).
create or replace function research.normalize_company_domain(d text)
returns text as $$
begin
    if d is null or d = '' then
        return null;
    end if;
    return nullif(
        rtrim(
            regexp_replace(
                split_part(regexp_replace(lower(trim(d)), '^https?://', ''), '/', 1),
                '^www\.', ''
            ),
            '.'
        ),
        ''
    );
end;
$$ language plpgsql immutable;

create or replace function research.known_companies_normalize()
returns trigger as $$
begin
    new.domain_normalized := research.normalize_company_domain(new.domain);
    new.updated_at := now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_known_companies_normalize on research.known_companies;
create trigger trg_known_companies_normalize
    before insert or update on research.known_companies
    for each row execute function research.known_companies_normalize();

-- Batch lookup the xref can use for a list of domains. Returns one row per input
-- domain with its status, defaulting to 'net-new' on a miss.
create or replace function research.check_company_domains(domains text[])
returns table (input_domain text, status text, company_name text, notes text) as $$
begin
    return query
    select d.dom as input_domain,
           coalesce(kc.status, 'net-new') as status,
           kc.company_name,
           kc.notes
    from unnest(domains) as d(dom)
    left join research.known_companies kc
        on research.normalize_company_domain(d.dom) = kc.domain_normalized;
end;
$$ language plpgsql;
