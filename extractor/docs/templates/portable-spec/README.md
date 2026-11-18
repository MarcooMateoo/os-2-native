<!-- Portable product specification (stage 3). Copy to
     projects/<Project>/OUTPUT/SPECS/<App>/portable-spec/README.md.
     Technology-neutral: no platform terms, module names, libraries,
     languages, colours, fonts or logos; secrets and personal data are
     described, never copied. Run scripts/check_portable_spec.py before
     showing it to the user. Delete these comments. -->

# <Product name>

<One paragraph: what the product does, for whom, and why.>

- **Status:** Draft, waiting for review
- **Prepared:** <date>, from the analysis of the existing application
  (reference `<run id>`)
- **Scope:** <the parts of the existing application this covers>

## How to read this specification

| # | Document | What it describes |
|---|---|---|
| 1 | [PRD.md](PRD.md) | Goals, users, features with acceptance criteria, journeys, business rules, quality requirements, open questions |
| 2 | [domain-model.md](domain-model.md) | The business information the product manages |
| 3 | [screens-and-navigation.md](screens-and-navigation.md) | Every screen, what it shows, what users can do, and where it leads |
| 4 | [behaviour.md](behaviour.md) | What the product does behind each user action and process, step by step |
| 5 | [integrations.md](integrations.md) | The external systems it exchanges information with |
| 6 | [configuration-and-jobs.md](configuration-and-jobs.md) | Settings, scheduled jobs and notifications |
| 7 | [glossary.md](glossary.md) | The business terms used |

## Identifiers

Features (`F1`; `A1` for administration features), journeys (`J1`), business
rules (`BR1`), behaviours (`B1`) and open questions (`Q1`) have stable IDs.
Use them when you refer to this specification.
