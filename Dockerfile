FROM python:3.12-slim

# python-magic needs libmagic's shared library at runtime to detect file types, since
# Drive downloads don't preserve extensions - see CLAUDE.md's "System dependency" note.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installed before the rest of the source so an unrelated code change doesn't
# invalidate this layer and force a full reinstall - requirements.txt's versions are
# pinned intentionally, so this only changes when they do.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# .dockerignore keeps every gitignored runtime file (data/, final/, logs/, .env, any
# *.json - the service account key, config/pdf_layout.json, config/column_mapping.json)
# out of this COPY, the same way git itself never tracks them - none of it belongs
# baked into an image, it's supplied at `docker run`/compose time instead (see
# docker-compose.yml).
COPY . .

# data/, final/, logs/, and config/ are all created on demand by paths.ensure_dirs()
# (cli.py's main() calls it before dispatching any command, and the web UI does the
# same) - mount them as volumes to persist state across container recreations; without
# one they're exactly as ephemeral as the container itself, the same as a machine that's
# never run the pipeline. config/ specifically must be bind-mounted as a *directory*,
# never as individual files - paths.py's own comment on CONFIG_DIR explains why (in
# short: atomic_write()'s rename-into-place fails outright, "Device or resource busy",
# if a container runtime has bind-mounted the target file itself rather than its parent
# directory - verified, not assumed). Runs as root, deliberately - the same "no auth,
# LAN-only" simplicity tradeoff CLAUDE.md already documents for the web UI itself, and
# it avoids bind-mount permission mismatches a non-root user would routinely hit on
# first run.
EXPOSE 5000

ENTRYPOINT ["python", "-m", "kvittomall"]
CMD ["webui"]
