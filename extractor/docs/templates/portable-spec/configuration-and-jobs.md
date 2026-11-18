<!-- Portable product specification (stage 3). Copy to
     projects/<Project>/OUTPUT/SPECS/<App>/portable-spec/configuration-and-jobs.md.
     Technology-neutral: no platform terms, module names, libraries,
     languages, colours, fonts or logos; secrets and personal data are
     described, never copied. Run scripts/check_portable_spec.py before
     showing it to the user. Delete these comments. -->

# Configuration, scheduled jobs and notifications

## Settings

| Setting | Meaning | Kind of value | Secret | Default for development | Varies per environment |
|---|---|---|---|---|---|
| <setting> | <meaning> | text / number / yes-no / list of emails | yes/no | <value, or "none"; never a secret or personal value> | yes/no |

## Scheduled jobs

| Job | What it does | When it runs | Behaviour |
|---|---|---|---|
| <job> | <purpose> | <schedule, or "to be confirmed"> | B<n> |

## Notifications

| Notification | Sent to | When | Content |
|---|---|---|---|
| <email> | <recipients, by role> | <trigger> | <subject and content in words> |
