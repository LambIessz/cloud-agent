FROM python:3.12-slim

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    CLOUD_AGENT_LLM_PRICING_CONFIG=/app/ops/prometheus/llm_pricing.example.yml

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY cloud_agent/requirements-container.txt /app/cloud_agent/requirements-container.txt
COPY cloud_agent/agent/requirements.txt /app/cloud_agent/agent/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/cloud_agent/requirements-container.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY cloud_agent/app /app/cloud_agent/app
COPY cloud_agent/agent /app/cloud_agent/agent
COPY cloud_agent/mock_data /app/cloud_agent/mock_data
COPY ops/prometheus/llm_pricing.example.yml /app/ops/prometheus/llm_pricing.example.yml

RUN addgroup --system cloudagent \
    && adduser --system --ingroup cloudagent --home /app --no-create-home cloudagent \
    && chown -R cloudagent:cloudagent /app

WORKDIR /app/cloud_agent/app

USER cloudagent

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/readyz', timeout=5).read()"

CMD ["python", "-X", "utf8", "-m", "uvicorn", "app_main:app", "--host", "0.0.0.0", "--port", "5000"]
