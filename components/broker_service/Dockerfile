# Image of the broker service.
# The build context is .local_assets/build/ of the project: update.sh fills it with git archive of this project
# into opt/gustaw/agent_broker/. The image holds no secret, no vault and no part of maya-agent-kit.
# A host with a vault mounts the vault and the vault tool beside the code (compose.override.yaml of the host).
FROM ubuntu:26.04

ARG AGENT_BROKER_COMMIT=unknown
ARG AGENT_BROKER_COMMIT_DATE=
ARG AGENT_BROKER_COMMIT_SUBJECT=
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH

# Python 3.14 in a venv.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

COPY opt /opt
WORKDIR /opt/gustaw/agent_broker/components/broker_service
# requirements.txt has the editable installs of the component client and of this component.
# The service finds config/ from its source folder, so it must stay an editable install.
# The vault tool (maya/tools/auth), if a host mounts it, needs cryptography and keyring: the component client installs them.
RUN pip install -r requirements.txt

# compose.yaml sets the user of the container and its HOME, a tmpfs.

ENV AGENT_BROKER_COMMIT=${AGENT_BROKER_COMMIT}
ENV AGENT_BROKER_COMMIT_DATE="${AGENT_BROKER_COMMIT_DATE}" \
    AGENT_BROKER_COMMIT_SUBJECT="${AGENT_BROKER_COMMIT_SUBJECT}"
ENTRYPOINT ["python", "-m", "agent_broker_service"]
