# Hugging Face Spaces (Docker) image for the public demo.
# The Space only needs this Dockerfile and README.md: it builds from the GitHub repo.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user

USER user
WORKDIR /home/user
RUN git clone --depth 1 https://github.com/mstnisa/clinicdesk-ai.git app
WORKDIR /home/user/app/agent
RUN pip install --no-cache-dir --user -r requirements.txt

# Public demo: fake data, shared dashboard token, strict limits.
# Add GROQ_API_KEY (and LLM_PROVIDER=groq) as Space secrets to use a real model.
ENV PATH="/home/user/.local/bin:$PATH" \
    DEMO_MODE=true \
    DATABASE_URL=sqlite:////tmp/clinicdesk.db \
    RATE_LIMIT_PER_MINUTE=12 \
    DAILY_LLM_CALL_BUDGET=400

EXPOSE 7860
CMD ["sh", "-c", "python -m scripts.seed_demo && uvicorn app.main:app --host 0.0.0.0 --port 7860 --proxy-headers --forwarded-allow-ips '*'"]
