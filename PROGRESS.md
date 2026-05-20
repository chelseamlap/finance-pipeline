# Progress

Last updated: 2026-05-20

## Current State

The finance pipeline has been designed and initially implemented as a local-first, deterministic parsing workflow for:

- Simplifi transaction CSV exports
- Amazon Order History Reporter exports
- Amazon Order History Exporter JSON/CSV exports
- Costco Receipt Downloader JSON exports
- Generic OrderPro exports for Target, Amazon, Walmart, Costco, and future stores

The intended repository is:

https://github.com/chelseamlap/finance-pipeline

The active local checkout is:

```text
/Users/chelsea.lapepeikis/Desktop/personal-repo/finance-pipeline
```

## What Was Built

- Python package skeleton with Typer CLI.
- Canonical transaction and retail item models.
- YAML-based category taxonomy and deterministic merchant/item rules.
- Simplifi category migration mapping.
- Spending class mapping.
- Source registry and schema alias config.
- Loaders for Simplifi, Amazon, Costco, Target manual, and generic OrderPro.
- OrderPro adapter that can infer retailer from `--store`, join order and item reports, and preserve source category fields.
- Allocation logic for order-level tax, shipping, fees, and discounts.
- Reconciliation logic for source totals and Simplifi transaction matching.
- Monthly CSV export writer.
- Pytest fixtures and tests for loaders, categorization, reconciliation, allocation, and CLI smoke behavior.
- README with architecture, monthly workflow, reconciliation philosophy, troubleshooting, and extension notes.

## Last Known Validation

The test suite passed from the generated working copy before publishing:

```text
10 passed
```

After copying into the Desktop GitHub clone, the test suite also passed:

```text
10 passed in 0.56s
```

## Important Note Before Resuming

At stop time, `git status` in the Desktop checkout showed the `finance_pipeline/` package files as deleted:

```text
D finance_pipeline/__init__.py
D finance_pipeline/categorize.py
D finance_pipeline/cli.py
D finance_pipeline/export.py
D finance_pipeline/loaders/...
D finance_pipeline/models.py
D finance_pipeline/normalize.py
D finance_pipeline/reconcile.py
D finance_pipeline/source_registry.py
```

Do not continue with first-run testing until this is understood. The safest first step next time is:

```bash
cd /Users/chelsea.lapepeikis/Desktop/personal-repo/finance-pipeline
git status -sb
git pull --ff-only
```

If the package deletion is accidental and GitHub has the complete pushed version, restore it with:

```bash
git restore finance_pipeline
```

Only do that if you confirm the deletion was not intentional.

## Not Yet Done

- Run the pipeline end-to-end on the real exports.
- Confirm real export schema coverage for every file in `~/Documents/store exports`.
- Decide where real raw exports should live locally.
- Review first reconciliation output and tune only explicit YAML rules.
- Add any missing schema aliases discovered from real exports.
- Add tests for any new real-world schema variants.

## Principles To Preserve

- Do not overwrite raw exports.
- Do not silently drop rows.
- Do not infer missing money.
- Do not create categories dynamically.
- Do not use source retailer categories as household categories unless explicitly configured.
- Prefer review files and explicit YAML changes over clever automation.
