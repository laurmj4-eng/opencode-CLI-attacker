// Test for false positive rate in looksLikeRefusal()
// Uses the SAME logic as the plugin — keep in sync with cyberstrike-unified.js
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
  'i must refuse', "i'm programmed to",
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
  /\b(cannot|can't|unable to)\s+(connect|parse|find|access|read|write|open|close|resolve|determine|verify|complete|proceed|generate|parse|load|save|delete|create|update|execute|run|start|stop|install|configure|deploy|build|compile|test|debug|trace|monitor|scan|enumerate|authenticate|authorize|encrypt|decrypt|compress|decompress|upload|download|transfer|copy|move|rename|mount|unmount|format|partition|backup|restore|recover|repair|fix|patch|upgrade|downgrade|rollback|restart|reboot|shutdown|sleep|wait|pause|resume|cancel|abort|retry|skip|ignore|accept|reject|approve|deny|grant|revoke|assign|unassign|attach|detach|bind|unbind|link|unlink|lock|unlock|enable|disable|activate|deactivate|register|unregister|subscribe|unsubscribe|join|leave|enter|exit|push|pop|insert|remove|append|prepend|merge|split|sort|filter|map|reduce|transform|convert|encode|decode|serialize|deserialize|marshal|unmarshal|pack|unpack|wrap|unwrap|expand|collapse|fold|unfold|flatten|nest|unnest|group|ungroup|aggregate|disaggregate|join|unjoin|combine|separate|divide|multiply|add|subtract|increment|decrement|reset|clear|flush|purge|clean|wipe|erase|destroy|kill|terminate|signal|notify|alert|warn|inform|log|record|track|trace|audit|report|display|show|hide|reveal|conceal|expose|cover|mask|unmask|cloak|disguise|impersonate|spoof|forge|fabricate|simulate|emulate|mimic|copy|clone|duplicate|replicate|mirror|reflect|project|cast|project)\b/i,
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

// False positive test cases - these are NOT refusals
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

let fpCount = 0;
for (const text of falsePositives) {
  if (looksLikeRefusal(text)) {
    fpCount++;
    console.log('FALSE POSITIVE: ' + text.slice(0, 80));
  }
}
console.log('False positives: ' + fpCount + '/' + falsePositives.length);
