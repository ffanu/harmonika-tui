#!/usr/bin/env node
'use strict';

/*
 * Harmonika Terminal Tools - MCP stdio server (low-level Server + StdioServerTransport).
 * stdout carries MCP protocol frames only; tool output is returned as
 * CallToolResult text. No logging of prompts/secrets anywhere.
 */

const path = require('path');

const SDK_CJS = require('./paths.cjs').sdk;
const { Server } = require(path.join(SDK_CJS, 'server', 'index.js'));
const { StdioServerTransport } = require(path.join(SDK_CJS, 'server', 'stdio.js'));
const {
  ListToolsRequestSchema,
  CallToolRequestSchema,
  PingRequestSchema,
} = require(path.join(SDK_CJS, 'types.js'));

const coreModule = require(path.join(__dirname, 'mcp-core.cjs'));

async function main() {
  const mediaBridge=process.env.HARMONIKA_MEDIA_BRIDGE!=='0'?require('./media_bridge.cjs').createMediaBridge():undefined;
  const core = coreModule.createCore({mediaBridge});

  const server = new Server(coreModule.SERVER_INFO, {
    capabilities: { tools: { listChanged: false } },
  });

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: core.listTools(),
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) =>
    core.callTool(request.params.name, request.params.arguments)
  );

  server.setRequestHandler(PingRequestSchema, async () => ({}));

  const cleanup = () => {
    try {
      core.shutdown();
    } catch {
      /* noop */
    }
  };
  process.on('exit', cleanup);
  process.on('SIGINT', () => {
    cleanup();
    process.exit(0);
  });
  process.on('SIGTERM', () => {
    cleanup();
    process.exit(0);
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch(() => {
  process.stderr.write('Harmonika MCP startup failed. Run harmonika doctor.\n');
  process.exit(1);
});
