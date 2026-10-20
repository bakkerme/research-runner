FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /opt/research-runner
COPY pyproject.toml build_support.py MANIFEST.in README.md AGENTS.md ./
COPY runner ./runner
COPY docs ./docs
COPY schemas ./schemas
COPY wheelhouse /wheelhouse
RUN pip install --no-index --find-links=/wheelhouse '.[server]' && rm -rf /wheelhouse
RUN chmod -R a+rX /opt/research-runner && useradd -u 10001 -m worker
USER worker
ENTRYPOINT []
