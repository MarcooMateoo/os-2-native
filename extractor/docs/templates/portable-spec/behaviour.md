<!-- Portable product specification (stage 3). Copy to
     projects/<Project>/OUTPUT/SPECS/<App>/portable-spec/behaviour.md.
     Technology-neutral: no platform terms, module names, libraries,
     languages, colours, fonts or logos; secrets and personal data are
     described, never copied. Run scripts/check_portable_spec.py before
     showing it to the user. Delete these comments. -->

# Behaviour

<One entry per operation of the existing application: every user action
on a screen, every operation behind it, every reusable operation and
scheduled process. IDs B1, B2, ... ; the mapping to the existing
application is kept in ../portable-spec-trace.md, which is not shared.>

## B1. <What it does, in a few words>

- **Triggered by:** <a user action on <screen>, another behaviour (B<n>), a schedule>
- **Inputs:** <name: meaning (required/optional)>
- **Result:** <what it returns or changes>
- **Who may run it:** <roles>
- **Business rules:** BR<n>, …

**Steps**
1. Find <records> where <condition>, sorted by <…>.
2. If <condition in plain words>:
   - yes: <steps>
   - no: <steps>
3. For each <record>: <steps>
4. Save <record> with <values>.
5. Notify <who> / send <email> / call <external system> (see integrations.md).

**When something goes wrong:** <what is undone, what the user is told,
what is recorded>.
