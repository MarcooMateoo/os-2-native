<!-- Portable product specification (stage 3). Copy to
     projects/<Project>/OUTPUT/SPECS/<App>/portable-spec/domain-model.md.
     Technology-neutral: no platform terms, module names, libraries,
     languages, colours, fonts or logos; secrets and personal data are
     described, never copied. Run scripts/check_portable_spec.py before
     showing it to the user. Delete these comments. -->

# Domain model

<The business information the product keeps, as concepts, not tables.>

## <Concept, e.g. Order>

<What it represents.>

| Field | Meaning | Type of value | Required | Rules |
|---|---|---|---|---|
| <field> | <meaning> | text (max <n>) / whole number / decimal (<p>,<s>) / date / date and time / yes-no / file / reference to <Concept> | yes/no | <rules> |

**Relationships:** <e.g. an Order belongs to one Customer; deleting a
Customer is not allowed while it has Orders.>

## Reference data

| List | Values | Notes |
|---|---|---|
| <e.g. Order status> | <Draft, Submitted, Approved, …> | <ordering, which are active> |
