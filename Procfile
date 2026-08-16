# Process types for the Sovereign service. Parsed by infra's checked-in Python
# helper and usable by nixpacks/any Procfile-aware runner. Stateless: a single
# web process, no Celery/scheduler/worker (unlike the main app).
web: uvicorn app.main:app --host 0.0.0.0 --port 8000
