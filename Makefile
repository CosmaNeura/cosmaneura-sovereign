.PHONY: install demo test serve
install:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
demo:
	python run_demo.py
test:
	python tests/test_pipeline.py
serve:
	uvicorn app.main:app --reload --port 8080
