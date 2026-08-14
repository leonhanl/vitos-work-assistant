FROM python:3.12-slim

# Mirror the repo layout so config.py's parents[4] resolves to /repo.
WORKDIR /repo

COPY apps/agent/pyproject.toml apps/agent/pyproject.toml
COPY apps/agent/src apps/agent/src
RUN pip install --no-cache-dir -e apps/agent

COPY apps/agent/skills apps/agent/skills

EXPOSE 8000
CMD ["uvicorn", "work_assistant.app:app", "--host", "0.0.0.0", "--port", "8000"]
