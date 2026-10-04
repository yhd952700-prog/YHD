# EAP Click-Through — Seed & Run Data

This package is **read-only against a running system**: it assumes a seeded, reachable
single-port gateway at `BASE_URL` (default `http://localhost:8080`). Nothing here seeds
state itself — seed is done **out-of-band** on the host.

## 1. Seed a human identity + credential

Human identity is pre-seeded out-of-band (there is **no UI registration**). From the repo root:

```bash
# Register (ACTIVE, human) identity + set its login password.
python scripts/register_human_identity.py --principal eap_admin --password-stdin
# then type the password on the prompt (or pipe: echo -n 's3cret' | ... --password-stdin)
```

Confirm the console reports it as login-eligible: `GET /v1/auth/config` →
`secret_store.configured == true` and `login_eligible_humans >= 1`.

## 2. Backend env vars the container needs

Set these on the persisted volume (they survive restart — EmployeeStore and the identity
registry are real and persisted):

| Env var | Purpose | Example |
|---|---|---|
| `LIUHAO_HUMAN_IDENTITIES_FILE` | Path (on the persisted volume) to the human identity registry file | `/data/human_identities.json` |
| `LIUHAO_AUTH_SECRETS_FILE` | Path (on the persisted volume) to the credential/secrets store | `/data/auth_secrets.json` |
| `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` | Integrity HMAC key for the human registry. **If unset**, the registry refuses all rows → `rows_refused` shows "0 admitted humans". | `openssl rand -hex 32` |
| `LIUHAO_KERNEL_POLICY_ENFORCE` | Arms the Policy C-3 kernel interception for the given tiers (P5 needs this). | `CRITICAL` |

> P9 honesty note: with `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` unset, the identity registry
> fails closed and refuses every row — the UI then truthfully shows "0 admitted humans". This is
> a seed/integrity issue, not a bug.

## 3. Obtain a token (two ways)

- **UI login** (what the click-through uses): fill `#os-principal` / `#os-secret`, click
  `button.os-btn`. The token is stored in `localStorage['liuhao.session']` and sent as
  `X-Liuhao-Token: Bearer <jwt>` (also `Authorization`).
- **Out-of-band token**: `python scripts/issue_console_token.py --principal eap_admin`
  (emits a JWT you can paste into the ApprovalCenter "token row", or use directly in API calls).

> Machine principals `system` / `liuhao-internal-service` are **refused** by
> `register_human_identity.py` and cannot hold sovereignty — a token minted for them is
> rejected with **400** on `POST /v1/policy/approvals`.

## 4. Install browsers + run

On the Docker+Playwright host (not the authoring env):

```bash
cd eap/01-browser-click-through
npm install                 # pulls @playwright/test
npx playwright install chromium   # browsers are NOT in the authoring env

# Point at the gateway and run:
BASE_URL=http://<host>:8080 EAP_PRINCIPAL=eap_admin EAP_SECRET='<password>' npx playwright test
```

`EAP_PRINCIPAL` / `EAP_SECRET` default to `eap_admin` / empty; supply the real password via env.

## 5. What the specs assume (gates)

- Gateway reachable at `BASE_URL`, root `/` serves the SPA, `/v1` is the API.
- SPA routing is query-string based: `/?p=overview|employees|approval|identity|goals|files`.
  There is **no** `/console` path prefix.
- Seeded: ≥1 human principal (login-eligible), and `LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL`
  so P5 has an armed `enforced_actions` set.
- An LLM provider is configured (P10 artifact generation needs execution to run).
