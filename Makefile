.PHONY: web run test eval up
web:  ; cd web && npm install && npm run build
run:  web ; PYTHONPATH=services/api:sdk/python:agents VARUNA_DATA_DIR=./data uvicorn varuna.main:app --port 8000
test: ; PYTHONPATH=services/api:sdk/python:agents python -m pytest services/api/tests -q
eval: ; PYTHONPATH=services/api:sdk/python:agents python eval/run_eval.py
up:   ; docker compose up --build
