FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PAPERLIFE_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m paperlife && mkdir /data && chown paperlife:paperlife /data
COPY paperlife ./paperlife
COPY web ./web
COPY policies ./policies
USER paperlife
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)"
CMD ["uvicorn","paperlife.main:app","--host","0.0.0.0","--port","8000","--proxy-headers","--forwarded-allow-ips=172.30.20.2"]
