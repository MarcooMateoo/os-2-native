<!-- Portable product specification (stage 3). Copy to
     projects/<Project>/OUTPUT/SPECS/<App>/portable-spec/PRD.md.
     Technology-neutral: no platform terms, module names, libraries,
     languages, colours, fonts or logos; secrets and personal data are
     described, never copied. Run scripts/check_portable_spec.py before
     showing it to the user. Delete these comments. -->

# <Product name>: product requirements

## 1. Purpose and goals

<The problem the product solves and the outcomes it must deliver.>

## 2. Users and roles

| Role | Who | What they can do |
|---|---|---|
| <role> | <people> | <permissions in plain words> |

## 3. Features

### F1. <Feature name>

<What it lets the user do and why it matters.>

**User stories**
- As a <role>, I want <goal>, so that <benefit>.

**Acceptance criteria**
- Given <situation>, when <action>, then <result>.
- <Validation, permissions, empty values and error cases.>

**Business rules:** BR<n>, … · **Screens:** <screen names> · **Behaviours:** B<n>, …

## 4. User journeys

### J1. <Journey>

1. <Step, citing features (F<n>) and screens.>

## 5. Business rules

| ID | Rule | Applies to |
|---|---|---|
| BR1 | <rule in plain words> | F<n>, B<n> |

## 6. Quality requirements

<Security (who may see and change what), audit trail, data retention,
performance, availability, accessibility, languages. Mark derived ones as
"(inferred)".>

## 7. Out of scope

<What the existing application does that is not part of this product.>

## 8. Open questions

| ID | Question | Why it matters | Affects |
|---|---|---|---|
| Q1 | | | F<n> |
