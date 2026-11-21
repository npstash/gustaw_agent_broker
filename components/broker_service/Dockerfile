# Image of the broker service.
# The build context is .local_assets/build/ of the project: update.sh fills it with git archive
# of this project and of maya/tools/auth, in the layout of a workspace. The layout keeps the
# relative paths of dependencies.txt valid: ../../../maya and ../../../vaults/vault-agent_broker.
# The image holds no secret and no vault. The vault is a mount (compose.yaml).
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

COPY workspace /workspace
ARG PROJECT=/workspace/projects/gustaw/gustaw_agent_broker
WORKDIR ${PROJECT}/components/broker_service
# requirements.txt has the editable installs of the component client and of this component.
# The service finds config/ from its source folder, so it must stay an editable install.
# The vault tool (maya/tools/auth) needs cryptography and keyring: the component client installs them.
RUN pip install -r requirements.txt

# The mount points of compose.yaml: the local configuration, HOME and the scratch folder of the TLS key.
# compose.yaml sets the user of the container.
RUN mkdir -p .local_assets/tls .local_assets/config .local_assets/home
ENV HOME=${PROJECT}/components/broker_service/.local_assets/home

ENV AGENT_BROKER_COMMIT=${AGENT_BROKER_COMMIT}
ENV AGENT_BROKER_COMMIT_DATE="${AGENT_BROKER_COMMIT_DATE}" \
    AGENT_BROKER_COMMIT_SUBJECT="${AGENT_BROKER_COMMIT_SUBJECT}"
ENTRYPOINT ["python", "-m", "agent_broker_service"]
