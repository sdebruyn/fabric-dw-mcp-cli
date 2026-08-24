# Hosting the MCP server

Most people never need this page. The MCP server normally runs on your own machine, started by your AI client over stdio, and there is nothing to host and nothing to secure.

This page is for the other case: you want one shared instance running on a server that other machines connect to. That means the HTTP transport, a bind address that is not loopback, and two things you have to get right that the local setup handles for you.

For the local setup, see [MCP server install](../install.md#mcp).

## Starting the HTTP transport

```bash
fabric-dw-mcp --transport http [--host 127.0.0.1] [--port 8000] \
  [--allowed-host HOST] [--allowed-origin ORIGIN]
```

Binding to anything other than a loopback address requires `FABRIC_MCP_ALLOW_REMOTE=1`. Without it the server refuses to start and says so.

The transport has **no built-in authentication and no TLS**. Anyone who can reach the port can call every tool the server exposes, using the Fabric credentials the server itself runs under. Always front it with an authenticating reverse proxy that terminates TLS. Nothing on this page replaces that.

Request bodies are capped at 4 MiB and anything larger is rejected with HTTP 413. In practice only a multi-megabyte `execute_sql` script or object definition reaches that. The stdio transport has no such limit.

## Host and Origin validation {#host-and-origin-validation}

On the default loopback bind the server validates the `Host` and `Origin` header of every request automatically. You do not configure that, and if you pass none of the options below, nothing changes it.

It is switched on for you **only because the bind address is loopback**. Bind on any other address and it is off: every request that reaches the port is served without checking which name it was addressed to.

### Why that matters on a private network

Here is the sequence, and note that none of it needs the port to be reachable from the internet:

1. You start the server with `FABRIC_MCP_ALLOW_REMOTE=1 --host 0.0.0.0`.
2. Someone on that network opens an unrelated web page in their browser.
3. That page's own domain resolves to the address your server listens on. The attacker owns the DNS record, so pointing it at a private address costs them nothing.
4. As far as the browser is concerned the MCP endpoint is now part of the attacker's site, so it lets the page send requests to it from inside your network.
5. With no `Host` or `Origin` check the server answers. The page can call any tool it exposes, `execute_sql` included, against your warehouse under the server's own Fabric credentials.

That is a DNS rebinding attack. Host validation stops it at step 5: the request carries `Host: <attacker domain>`, which is not a name you allowed, so the server returns HTTP 421 instead of running the tool.

### Turning it on

Pass `--allowed-host`, naming the host clients actually use to reach the server:

```bash
FABRIC_MCP_ALLOW_REMOTE=1 fabric-dw-mcp --transport http \
  --host 0.0.0.0 \
  --allowed-host mcp.example.com
```

The option is repeatable, once per name that should be accepted:

```bash
... --allowed-host mcp.example.com --allowed-host 192.0.2.10
```

A value written without a port covers both a request sent straight to the port (`Host: mcp.example.com:8000`) and one forwarded by a reverse proxy on 80 or 443 (`Host: mcp.example.com`). Write the port yourself, as in `--allowed-host mcp.example.com:8000`, to accept only that one. IPv6 addresses work with or without brackets.

Values are checked at startup. A name that could never appear in a `Host` header, an empty one from an unset environment variable, or a wildcard such as `*.example.com`, is refused with a usage error rather than accepted into an allowlist that would then refuse every client.

Leaving `--allowed-host` off a non-loopback bind still starts the server, so nothing that runs today stops running, but it logs a warning at startup saying validation is off.

### `--allowed-host` replaces the automatic allowlist

Passing the option **replaces** whatever allowlist would otherwise apply; it does not add to it. That matters on a loopback bind, where it switches off the built-in `127.0.0.1` / `localhost` / `[::1]` entries and empties the Origin allowlist along with them. So:

```bash
fabric-dw-mcp --transport http --host 127.0.0.1 --allowed-host proxy.internal
```

serves `Host: proxy.internal` and answers HTTP 421 to `Host: 127.0.0.1:8000`, and a local browser client that worked before now gets HTTP 403 until you add `--allowed-origin`.

That is the intended behaviour, and it is the fix for one real case: a reverse proxy on the same machine that forwards a different `Host` header to a loopback bind. Without the option those requests are rejected with HTTP 421. With it, name every host the server should answer to, including the loopback ones if local clients also connect directly:

```bash
fabric-dw-mcp --transport http --host 127.0.0.1 \
  --allowed-host proxy.internal \
  --allowed-host 127.0.0.1 \
  --allowed-host localhost
```

## Browser-based clients and `--allowed-origin` {#allowed-origin}

`--allowed-host` on its own also applies the strictest Origin policy there is: any request carrying an `Origin` header is refused with HTTP 403. Most non-browser MCP clients do not send that header, so this costs them nothing and it is what you want for a server that only ever talks to them.

Treat "browser-based" broadly. An Electron renderer, a VS Code webview and a `fetch`-based Deno or Node client all send an `Origin`, and those are common MCP client shapes. If any client of yours does, name its origin:

```bash
... --allowed-host mcp.example.com --allowed-origin https://client.example.com
```

`--allowed-origin` is repeatable and requires `--allowed-host`. On its own it is rejected at startup, because origins are only consulted once host validation is on and a server with an empty host allowlist could not answer anything.

Unlike a host, an origin is matched **exactly**. A web origin is its scheme, host and port together, so `https://app.example.com` and `https://app.example.com:8443` are two different origins and allowing one does not allow the other. That is deliberate: a dev server or a second application on a high port of the same machine is a different security principal, and widening the port would hand it the right to drive every tool. Repeat the option once per origin instead. The default port for the scheme is optional, since browsers omit it: `https://app.example.com:443` and `https://app.example.com` mean the same thing.

## What this does and does not protect

Host and Origin validation checks which name a request was addressed to. It never checks who sent it. A client that knows the right host name still reaches every tool.

So the reverse proxy is not optional, and neither are the other guards. On a shared instance, consider `FABRIC_MCP_READONLY=1` and leaving `FABRIC_MCP_ALLOW_DESTRUCTIVE` unset, and restrict the reachable workspaces with `FABRIC_MCP_WORKSPACES`. All three are described under [security environment variables](../install.md#security-environment-variables).

If clients get HTTP 421 or 403 after you turn validation on, see [HTTP 421 or 403 from the HTTP transport](../troubleshooting.md#http-421-or-403-from-the-http-transport).
