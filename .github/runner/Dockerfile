# Flock's self-hosted CI runner. See README.md in this folder.
#
# The upstream image gives the `runner` user passwordless sudo and keeps the runner's own files
# writable by it. This image removes sudo and makes the installed runner root-owned, so a job can't
# become root or tamper with the copy the next job starts from.
FROM ghcr.io/actions/actions-runner:2.338.2@sha256:6d0b2d80d80f143f77dcb676e50920100bbb547e39e883454d57f45ab8c31300

USER root

# The tests validate the deployment's Compose files with `docker compose config`, which needs the
# Compose plugin but no Docker daemon. Jobs still get no daemon or socket.
ARG COMPOSE_VERSION=v5.7.0
RUN set -eux; \
    case "$(uname -m)" in \
      x86_64) arch=x86_64; sha=7758048ad38e580f22559603312874fec8bb638d021a9e64182385fc17cc12a2 ;; \
      aarch64) arch=aarch64; sha=15861535d14e26555736b49376c95e14bfd94285366c8446bfac7156a972e76f ;; \
    esac; \
    plugin=/usr/local/lib/docker/cli-plugins/docker-compose; \
    mkdir -p "$(dirname "$plugin")"; \
    curl -fsSL -o "$plugin" "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-$arch"; \
    echo "$sha  $plugin" | sha256sum -c -; \
    chmod 0755 "$plugin"

RUN set -eux; \
    rm -rf /usr/bin/sudo /usr/bin/sudoedit /etc/sudoers /etc/sudoers.d; \
    find / -xdev -type f -perm /6000 -exec chmod a-s {} +; \
    gpasswd -d runner sudo; \
    gpasswd -d runner docker; \
    mv /home/runner /opt/actions-runner; \
    chown -R root:root /opt/actions-runner; \
    chmod -R go-w /opt/actions-runner; \
    install -d -o runner -g runner -m 0700 /home/runner

COPY --chmod=0755 entrypoint.sh /usr/local/bin/flock-runner

# The entrypoint starts as root only to read the GitHub App key, then runs everything else as
# `runner`. Compose drops every capability except the two it needs to switch user.
WORKDIR /home/runner
ENTRYPOINT ["/usr/local/bin/flock-runner"]
