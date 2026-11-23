# Kong gateway image for the cloud (Azure Container Apps).
# Locally docker-compose mounts kong.yml directly; in the cloud the upstream API URL and the
# JWT secret come from environment variables and are written into the config at start-up.
FROM kong:3.9
COPY kong.yml /kong/kong.tmpl.yml
ENV KONG_DATABASE=off \
    KONG_DECLARATIVE_CONFIG=/tmp/kong.yml \
    KONG_PROXY_LISTEN=0.0.0.0:8000 \
    KONG_ADMIN_LISTEN=127.0.0.1:8001 \
    KONG_PROXY_ACCESS_LOG=/dev/stdout \
    KONG_PROXY_ERROR_LOG=/dev/stderr \
    API_URL=http://api:8000 \
    JWT_SECRET=change-me-dev-secret-at-least-32-bytes!! \
    GATEWAY_SECRET=local-dev-gateway-secret
EXPOSE 8000
CMD ["sh", "-c", "sed -e \"s#http://api:8000#${API_URL}#\" -e \"s#change-me-dev-secret-at-least-32-bytes!!#${JWT_SECRET}#\" -e \"s#local-dev-gateway-secret#${GATEWAY_SECRET}#\" /kong/kong.tmpl.yml > /tmp/kong.yml && exec /docker-entrypoint.sh kong docker-start"]
