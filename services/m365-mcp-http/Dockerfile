FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir -e .

EXPOSE 8001
CMD ["python", "-m", "m365_mcp.server"]
