if docker container inspect vitos-atlassian-mcp >/dev/null 2>&1; then
  docker kill vitos-atlassian-mcp >/dev/null 2>&1 || true
  docker rm vitos-atlassian-mcp
fi

docker run -d \
  --name vitos-atlassian-mcp \
  --restart unless-stopped \
  -p 0.0.0.0:9000:9000 \
  --env-file ./.jira.env \
  -e TOOLSETS=jira_service_desk,jira_projects,jira_agile \
  -e ENABLED_TOOLS=jira_get_request_types,jira_get_request_type_fields,jira_create_customer_request,jira_create_version,jira_update_version,jira_create_sprint \
  ghcr.io/sooperset/mcp-atlassian:latest \
  --transport streamable-http \
  --port 9000
