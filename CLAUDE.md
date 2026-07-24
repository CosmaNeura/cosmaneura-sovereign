# CLAUDE.md — cosmaneura-sovereign

## Qué es

Una API de información de salud **guiada por la enseñanza católica**, con arquitectura de
"Soberano Hobbesiano": cada request se enruta a uno de seis agentes de IA especialistas y
luego se arbitra por conformidad doctrinal. **Magisterium AI** es la autoridad doctrinal /
juez final; un LLM de razonamiento elegible para BAA (Anthropic Claude por defecto) hace la
orquestación mecánica y el ruteo. White-label sobre MyCatholicDoctor.

Dos productos, un solo codebase, seleccionados por persona:
- `patient` — build de consumidor, solo SOC 2, sin HIPAA, sin PHI.
- `provider` — build clínico, SOC 2 + HIPAA, PHI permitido, requiere LLM cubierto por BAA.

Es explícitamente un **scaffold de ingeniería**, no un producto clínico o de cumplimiento
certificado. Arranca en `MOCK_MODE=true` para que todo el pipeline corra end-to-end sin
credenciales.

## Stack

- Python 3.13, FastAPI + Uvicorn (un solo proceso web sin estado; sin DB, sin worker/scheduler).
- Pydantic v2 + pydantic-settings (config 12-factor por env).
- Auth: PyJWT `[crypto]` — JWT/OIDC (RS256/ES256 contra JWKS del IdP), más modo dev HS256.
- httpx (async), beautifulsoup4 + lxml (job de refresh del directorio de partners).
- De-id de PHI: azure-health-deidentification + azure-identity (opcional; baseline regex por defecto).
- LLM: Anthropic Messages API (default, BAA-eligible vía ZDR) o Azure OpenAI.
- Deploy: imagen nixpacks → Azure Container Apps, infra en Terraform, CI GitHub Actions con OIDC de Azure.
- Deps con rangos `~=` en `requirements.txt`. No hay lockfile. **Puro Python — sin Node.**

## Estructura

```
app/
  main.py            App FastAPI — POST /chat, GET /healthz, opcional GET /demo
  config.py          Settings (env), enums Persona/SovereignMode/AuthMode, ComplianceProfile
  schemas.py         Modelos request/response + Intent, DoctrinalSensitivity
  sovereign/         router.py · arbiter.py · orchestrator.py (el "Soberano")
  agents/            base.py · referral_agent · concierge_agent · engagement_agent ·
                     ethics_agent · visit_prep_agent · plan_workbench_agent
  clients/           llm · magisterium · nppes · clinicaltrials · pubmed · partner_directory
  compliance/        phi.py (de-id + tripwire de PHI) · audit.py · safety.py · netsec.py
  security/          auth.py (TokenVerifier, Principal, resolución OIDC/dev-HS256)
tests/               test_pipeline.py (mock-mode, offline)
tools/               mint_dev_token.py · scrape_mycatholicdoctor.py · run_demo_web.sh
infra/               Terraform (Azure) + scripts/
run_demo.py          Demo end-to-end de 8 escenarios
```
Docs: `README.md`, `ARCHITECTURE.md`, `SETUP_AND_HANDOFF.md`, `DEMO.md`, `DEPLOY_AZURE.md`.

## Comandos (Makefile)

```bash
make install    # python -m venv .venv && pip install -r requirements.txt
make demo       # python run_demo.py — 8 escenarios por el pipeline completo
make test       # python tests/test_pipeline.py
make serve      # uvicorn app.main:app --reload --port 8080
```

Correr scripts directamente requiere `PYTHONPATH=.`:
```bash
PYTHONPATH=. python run_demo.py
PYTHONPATH=. uvicorn app.main:app --reload
PYTHONPATH=. python -m pytest tests/ -q   # como corre CI (pytest, no el target make)
```

**No hay linter/formatter configurado** (sin ruff/black/mypy/pyproject.toml).

## Notas de arquitectura

- **Entrypoint:** `app/main.py` → singleton `Sovereign` en `app/sovereign/orchestrator.py`.
- **Pipeline (`Sovereign.handle`):** check de emergencia (corta a 911/988 antes de cualquier gate
  de auth) → resolución de persona (el principal JWT verificado sobreescribe la persona del body)
  → gate de seguridad (provider/PHI requiere identidad verificada o el switch dev
  `allow_provider_persona` + `baa_attested`) → ruteo (intent + tier de sensibilidad doctrinal;
  ETHICS_CONSULT fuerza tier HIGH siempre) → dispatch (DOCTRINAL → Magisterium directo;
  REFERRAL/CONCIERGE/ENGAGEMENT/ETHICS_CONSULT/VISIT_PREP/PLAN_WORKBENCH → agente; SMALL_TALK →
  LLM) → arbitraje doctrinal por Magisterium sobre una copia **con PHI removido** → disclaimer de
  seguridad → auditoría con hash de contenido → egress.
- **La frontera de PHI es central:** `ComplianceProfile.phi_boundary_enforced` significa que los
  identificadores se de-identifican antes de que cualquier llamada salga de la frontera cubierta
  por BAA (Magisterium, ClinicalTrials.gov, NPPES). Magisterium NO es business associate: solo ve
  texto de-identificado. `assert_no_phi` en `compliance/phi.py` es un tripwire que lanza
  `PHIBoundaryViolation`.
- **Modos del Soberano** (`SovereignMode`): `HYBRID` (default) vs `LITERAL` (Magisterium en cada request).
- **Modos de auth** (`AuthMode`): `disabled` (dev/patient), `dev_hs256` (local), `oidc` (prod, requerido para provider/PHI).
- **Hardening en main.py:** CORS deny-by-default (nunca `*`), límite de body 256 KB, security
  headers, 401 genéricos que nunca revelan detalle del token.
- Convenciones: `from __future__ import annotations`, async en todo, docstrings explicativos,
  settings vía `@lru_cache get_settings()`, clients mockeables vía `MOCK_MODE`.

## Config

`requirements.txt`, `Makefile`, `Procfile` (`web:`), `nixpacks.toml` (pin Python 3.13),
`.env.example` / `.env.demo.local` / `.env.production.backend.example`,
`.github/workflows/build_and_publish.yml`, `infra/*.tf`.
