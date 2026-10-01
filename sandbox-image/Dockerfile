FROM python:3.12-slim

RUN useradd --create-home --uid 10001 agent
USER agent
WORKDIR /workspace
CMD ["sleep", "infinity"]
