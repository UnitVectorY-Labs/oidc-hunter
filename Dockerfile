# Use a builder image to create an isolated virtual environment with the app installed
FROM python:3.15.0b4-slim AS builder

# Build argument for version injection
ARG VERSION=dev

# Python runtime defaults
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_VERSION=${VERSION}

# Set the working directory inside the container
WORKDIR /app

# Create a virtual environment for the application
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the project into the virtual environment
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && \
    pip install .

# Use a slim runtime image for running the application
FROM python:3.15.0b4-slim AS runtime

# Python runtime defaults
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Set the working directory inside the container
WORKDIR /app

# Copy the virtual environment from the builder image
COPY --from=builder /opt/venv /opt/venv

# Create an unprivileged runtime user
RUN groupadd --system app && useradd --system --gid app --uid 10001 app
USER 10001:10001

# Run the Python application
CMD ["python", "-m", "main"]
