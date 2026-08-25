# Compliance badges

Dpro GmbH badges, reused from the Flowxtra registration page.

| File | Claim |
|---|---|
| `gdpr-dsgvo.png` | DSGVO & GDPR compliant |
| `eu-ai-act.png` | EU AI Act compliant |

Each is a white card with rounded, transparent corners — so they stay legible on
GitHub's dark theme without a second variant. Native size is 169×46; the README
renders them at `height="34"`.

## Three badges were removed

`esign.png` and `eidas.png` were about **electronic signatures** — a Flowxtra
capability. Tel-Agent is a gateway between a phone line and an AI agent and does
not sign anything, so those two made a claim the software has no surface for.
`aes-256.png` read "AES **verified** 256", which implies an external audit of a
crypto implementation, and Tel-Agent is pre-alpha with no release. All three
files and their README lines are gone.

GDPR and the AI Act are, for a self-hosted product, properties of a deployment
rather than of a repository — the operator chooses the models, the region and the
retention. Worth keeping in mind before the badges are read as a certification.
