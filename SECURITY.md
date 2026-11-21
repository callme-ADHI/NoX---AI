# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of NoX and its users seriously.

If you discover a security vulnerability in NoX:
1. **Do NOT** open a public issue or discuss it in public channels.
2. Email the maintainer directly with details of the vulnerability, reproduction steps, and potential impact.
3. Include information on:
   - Affected component(s)
   - Step-by-step reproduction instructions or proof-of-concept
   - Impact assessment
4. We will acknowledge receipt of your vulnerability report within 48 hours and provide an estimated timeline for a security patch.

## Privacy & Security Principles

- **Local-Only by Default**: Personal data (emails, chats, notes, calendar events, private repo code) is never transmitted to cloud endpoints without explicit consent.
- **Guardrails**: All model inputs and outputs are scanned for prompt injections, credential leaks, and Indian PII (Aadhaar, PAN, banking details).
- **Audit Logging**: High-impact operations are recorded in an append-only, SHA-256 forward-hash chain.
- **Approvals & Kill Switch**: Guarded writes require single-use approval tokens. In case of unexpected behavior, `nx panic` immediately engages emergency containment.
