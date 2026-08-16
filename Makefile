.PHONY: install demo demo-dashboard test serve
install:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
demo:
	python run_demo.py
# Embedded-panel demo: the Sovereign panel mounted inside a stand-in for the
# Cosmas notetaking dashboard. Runs in MOCK_MODE with synthetic records — no
# credentials needed. Open http://localhost:8080/demo/dashboard
demo-dashboard:
	@echo "→ open http://localhost:8080/demo/dashboard"
	DEMO_UI_ENABLED=true ALLOW_PROVIDER_PERSONA=true PYTHONPATH=. \
		uvicorn app.main:app --port 8080
test:
	python tests/test_pipeline.py
serve:
	uvicorn app.main:app --reload --port 8080
