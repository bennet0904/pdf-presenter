FROM python:3.13-alpine

# Unprivilegierter Benutzer; /data gehört ihm, damit das benannte Volume es erbt.
RUN adduser -D -H -u 10001 crash && mkdir /data && chown crash:crash /data

COPY --chmod=755 crash_collector.py /usr/local/bin/crash_collector.py

USER crash
ENV CRASH_BIND=0.0.0.0:8765 \
    CRASH_DB=/data/reports.sqlite3 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"

ENTRYPOINT ["crash_collector.py"]
CMD ["serve"]
