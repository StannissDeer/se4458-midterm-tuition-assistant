FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY indexer.py server.py ./
COPY docs ./docs
# index is built at image build time, before any query
RUN python indexer.py
ENV MCP_PORT=8100
EXPOSE 8100
CMD ["python", "server.py"]
