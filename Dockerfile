FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy the project configuration first
COPY pyproject.toml uv.lock ./

# Install the project's dependencies using the lockfile and settings
RUN uv sync --frozen --no-install-project --no-dev

# Copy the rest of the application code
COPY main.py utils.py ./
COPY templates/ ./templates/

# Place the virtual environment in the path
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

