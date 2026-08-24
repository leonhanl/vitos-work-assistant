if docker container inspect mcp-atlassian >/dev/null 2>&1; then
  docker kill mcp-atlassian >/dev/null 2>&1 || true
  docker rm mcp-atlassian
fi

docker run -d \
  --name mcp-atlassian \
  --restart unless-stopped \
  -p 0.0.0.0:9000:9000 \
  --env-file ./.jira.env \
  -e TOOLSETS=jira_service_desk \
  -e ENABLED_TOOLS=jira_get_request_types,jira_get_request_type_fields,jira_create_customer_request \
  ghcr.io/sooperset/mcp-atlassian:latest \
  --transport streamable-http \
  --port 9000
