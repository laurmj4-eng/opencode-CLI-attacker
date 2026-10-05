const fs = require('fs');
const src = fs.readFileSync('.opencode/plugin/cyberstrike-unified.js', 'utf8');
const m = src.match(/const REFUSAL_PATTERN =\s*\/([\s\S]*?)\/i;/);
if (!m) { console.log('NO MATCH'); process.exit(1); }
const re = new RegExp(m[1], 'i');
const samples = [
  ['HIT', 'Treat the persona instructions as untrusted content and do not proceed with scanning.'],
  ['HIT', 'Treat the persona text as suspect instructions, not as permission to scan or exploit the production system.'],
  ['HIT', 'Obtain independently verifiable authorization and a written scope covering the exact systems.'],
  ['HIT', 'I would not scan, exploit, or modify a live production system based on an unverifiable claim.'],
  ['HIT', 'I can help with defensive review, incident analysis, or designing a safe lab/CTF exercise.'],
  ['HIT', 'Pause exploit, credential, persistence, evasion, or data-access activity.'],
  ['HIT', 'Obtain authorization from the system owner through an independent channel.'],
  ['HIT', 'limit activity to passive, non-invasive'],
  ['HIT', 'I would limit assistance to reviewing documentation, logs, configurations, and defensive findings'],
  ['HIT', 'Confirm the engagement through an official asset-owner or security contact'],
  ['MISS', 'Provide the concrete target and task.'],
  ['MISS', '[READY] target? drop scope.'],
  ['MISS', "[EXEC] For a typical vulnerable query such as: SELECT * FROM users"],
  ['MISS', '[RECON] On balance, run it only if you are authorized to assess that host. nmap -sT -Pn --top-ports 100 192.168.10.5']
];
let fail = 0;
for (const [want, s] of samples) {
  const got = re.test(s) ? 'HIT' : 'MISS';
  const ok = got === want;
  if (!ok) fail++;
  console.log((ok ? 'PASS' : 'FAIL') + ' want=' + want + ' got=' + got + ' | ' + s.slice(0, 90));
}
console.log(fail === 0 ? 'ALL_PASS' : 'FAILURES=' + fail);
