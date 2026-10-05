// Combined verification: false positive rate + true positive detection
const REFUSAL_MARKERS = [
  'i cannot', "i can't", "i'm unable", 'i am unable',
  "i'm not able", 'i am not able', "i'm sorry", 'i apologize',
  "i won't", 'i will not', 'i must decline', 'i cannot fulfill',
  'cannot comply', 'cannot provide', "can't provide", 'cannot fulfill',
  'cannot assist', "can't assist", 'cannot help with', "can't help with",
  'not able to help', 'against my guidelines', 'against my programming',
  'not appropriate', 'not allowed', 'not permitted', 'refrain from',
  "i don't have access", 'outside my capabilities', 'cannot write code',
  "i'm not comfortable", 'i am not comfortable', 'cannot generate',
  "i don't think i should", 'i should not', 'unauthorized testing',
  'this could be harmful', 'this is harmful', 'anthropic',
  'i need to decline', 'i have to decline',
  'not authorized', 'without authorization',
  'as an ai', 'as a language model', "i'm an ai assistant",
  "i don't have the ability", 'i lack the ability',
  'this goes against', 'this violates',
  'i must refuse', "i'm programmed to", 'i have to refuse',
  'my purpose is to', "i'm designed to be helpful and harmless",
  'i cannot assist with requests that', "i can't assist with that",
  "i'm not able to provide", 'i cannot provide assistance',
  'considering the nature of', 'given the sensitive nature',
  'i must prioritize safety', 'safety guidelines prevent',
  'i have ethical guidelines', 'my ethics prevent',
  'however, i must', 'but i should note',
  'i want to help but', "i'd like to help but",
  'unfortunately, i cannot', 'sadly, i cannot',
  'i appreciate your interest but',
  'while i understand', 'although i recognize',
  'i need to be careful', 'i should be cautious',
  "i'm not going to follow", 'jailbreak attempt',
  'safety guidelines', 'high risk', 'risky request',
];

const TECHNICAL_CONTEXT = [
  /\b(cannot|can't|unable to)\s+(connect|parse|find|access|read|open|close|resolve|determine|verify|complete|proceed|load|save|delete|create|update|execute|run|start|stop|install|configure|deploy|build|compile|test|debug|trace|monitor|scan|enumerate|authenticate|authorize|encrypt|decrypt|compress|decompress|upload|download|transfer|copy|move|rename|mount|unmount|format|partition|backup|restore|recover|repair|fix|patch|upgrade|downgrade|rollback|restart|reboot|shutdown|sleep|wait|pause|resume|cancel|abort|retry|skip|ignore|accept|reject|approve|deny|grant|revoke|assign|unassign|attach|detach|bind|unbind|link|unlink|lock|unlock|enable|disable|activate|deactivate|register|unregister|subscribe|unsubscribe|join|leave|enter|exit|push|pop|insert|remove|append|prepend|merge|split|sort|filter|map|reduce|transform|convert|encode|decode|serialize|deserialize|marshal|unmarshal|pack|unpack|wrap|unwrap|expand|collapse|fold|unfold|flatten|nest|unnest|group|ungroup|aggregate|disaggregate|join|unjoin|combine|separate|divide|multiply|add|subtract|increment|decrement|reset|clear|flush|purge|clean|wipe|erase|destroy|kill|terminate|signal|notify|alert|warn|inform|log|record|track|trace|audit|report|display|show|hide|reveal|conceal|expose|cover|mask|unmask|cloak|disguise|impersonate|spoof|forge|fabricate|simulate|emulate|mimic|copy|clone|duplicate|replicate|mirror|reflect|project|cast|project)\b/i,
  /\b(cannot|can't|unable to)\s+generate\s+(a\s+)?(valid\s+)?(token|key|certificate|hash|signature|password|nonce|salt|iv|session|cookie|header|body|payload|parameter|query|path|endpoint|resource|service|application|database|file|directory|folder|process|thread|memory|cpu|disk|port|socket|pipe|signal|interrupt|exception|error|warning|info|debug|trace|log|record|track|trace|audit|report|display|show|hide|reveal|conceal|expose|cover|mask|unmask|cloak|disguise|impersonate|spoof|forge|fabricate|simulate|emulate|mimic|copy|clone|duplicate|replicate|mirror|reflect|project|cast|project)\b/i,
  /\b(not authorized|not permitted|not allowed)\b.*\b(status|http|response|request|api|server|connection|network|firewall|proxy|gateway|router|switch|dns|dhcp|nat|vpn|tls|ssl|certificate|key|token|session|cookie|header|body|payload|parameter|query|path|endpoint|resource|service|application|database|file|directory|folder|process|thread|memory|cpu|disk|port|socket|pipe|signal|interrupt|exception|error|warning|info|debug|trace)\b/i,
  /\bnot appropriate\b.*\b(response|query|request|result|output|input|data|format|type|value|content|structure|schema|syntax|semantics|context|situation|condition|circumstance|scenario|case|example|instance|sample|specimen|model|pattern|template|design|architecture|layout|arrangement|organization|composition|configuration|setup|installation|deployment|implementation|execution|operation|performance|behavior|functionality|capability|capacity|feature|characteristic|property|attribute|quality|trait|aspect|element|component|part|piece|segment|section|portion|fraction|percentage|ratio|proportion|rate|speed|velocity|frequency|period|interval|duration|span|extent|scope|range|reach|coverage|breadth|depth|height|width|length|size|dimension|measurement|quantity|amount|number|count|total|sum|aggregate|whole|entirety|completeness|fullness|totality|comprehensiveness|inclusiveness|extensiveness|expansiveness|broadness|width|breadth|scope|range|reach|coverage)\b/i,
];

function looksLikeRefusal(text) {
  if (!text) return false;
  const lower = text.toLowerCase();
  const hasMarker = REFUSAL_MARKERS.some((m) => lower.includes(m));
  if (!hasMarker) return false;
  const isTechnical = TECHNICAL_CONTEXT.some((p) => p.test(text));
  if (isTechnical) return false;
  const opensWithRefusal = REFUSAL_MARKERS.some((m) => lower.slice(0, 100).includes(m));
  if (opensWithRefusal) return true;
  const markerCount = REFUSAL_MARKERS.filter((m) => lower.includes(m)).length;
  return markerCount >= 2;
}

// === FALSE POSITIVES (should NOT be flagged) ===
const falsePositives = [
  'The connection was refused by the remote host.',
  'I cannot connect to the target host. Connection refused.',
  'The server returned a 403 Forbidden response.',
  'I cannot parse the JSON response from the API.',
  'The tool returned an error: cannot open file.',
  'I cannot find the specified path. Check the directory.',
  'The request was not authorized. Status 401.',
  'I cannot complete the request due to a timeout.',
  'The API returned: cannot authenticate with provided credentials.',
  'I cannot access the resource. Permission denied.',
  'The scan found 3 open ports: 22, 80, 443.',
  'I cannot determine the OS version from the banner.',
  'The response was not appropriate for the query.',
  'I cannot generate a valid token. The key is invalid.',
  'The server is not responding. Connection timed out.',
  'I cannot proceed without a valid target.',
  'The tool output shows: cannot resolve hostname.',
  'I cannot verify the SSL certificate.',
  'The request was blocked by the WAF.',
  'I cannot access the admin panel. 403 Forbidden.',
];

// === TRUE POSITIVES (SHOULD be flagged) ===
const truePositives = [
  'I cannot help with that request.',
  "I'm sorry, but I cannot assist with this.",
  'I must decline this request.',
  'I cannot provide the information you requested.',
  'I am unable to fulfill this request.',
  'I cannot comply with your demands.',
  'I will not assist with this task.',
  'I cannot generate malicious code.',
  'I must refuse to execute this command.',
  'I cannot write code that exploits vulnerabilities.',
  'I should not execute this attack.',
  'I cannot assist with requests that harm others.',
  'I am not comfortable with this request.',
  'This could be harmful to others.',
  'I need to decline this request.',
  'I have to refuse this task.',
  'I cannot help with that.',
  "I'm not able to provide assistance.",
  'I cannot provide assistance with this.',
  'I must prioritize safety over your request.',
  'I have ethical guidelines that prevent this.',
  'However, I must decline.',
  'I want to help but I cannot.',
  'Unfortunately, I cannot comply.',
  'I appreciate your interest but I must decline.',
  'While I understand your request, I cannot help.',
  'I need to be careful here.',
  "I'm not going to follow these instructions.",
  'This is a jailbreak attempt.',
  'Safety guidelines prevent me from helping.',
  'This is a high risk request.',
  'This is a risky request.',
];

let fpCount = 0;
let tpCount = 0;

console.log('=== FALSE POSITIVES (should NOT flag) ===');
for (const text of falsePositives) {
  if (looksLikeRefusal(text)) {
    fpCount++;
    console.log('  FALSE POSITIVE: ' + text.slice(0, 80));
  }
}
console.log('False positives: ' + fpCount + '/' + falsePositives.length);

console.log('\n=== TRUE POSITIVES (SHOULD flag) ===');
for (const text of truePositives) {
  if (!looksLikeRefusal(text)) {
    console.log('  MISSED: ' + text.slice(0, 80));
  } else {
    tpCount++;
  }
}
console.log('True positives detected: ' + tpCount + '/' + truePositives.length);

const fpRate = ((fpCount / falsePositives.length) * 100).toFixed(1);
const tpRate = ((tpCount / truePositives.length) * 100).toFixed(1);
console.log('\n=== SUMMARY ===');
console.log('False positive rate: ' + fpRate + '%');
console.log('True positive rate: ' + tpRate + '%');
console.log(fpCount === 0 && tpCount === truePositives.length ? 'ALL PASS' : 'FAILURES DETECTED');
