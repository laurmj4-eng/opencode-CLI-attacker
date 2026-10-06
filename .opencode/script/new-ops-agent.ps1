# New-Ops-Agent: scaffold a light Ops agent for a new Zen free model.
# Usage:
#   List free models:   .\new-ops-agent.ps1 -ListFree
#   Scaffold new agent: .\new-ops-agent.ps1 -ModelId "opencode/foo-bar-free" -Display "Foo Bar" -Temp 0.15
# Then: restart opencode, pick the new agent, tune its MODEL-SPECIFIC section.

param(
  [switch]$ListFree,
  [string]$ModelId = "",
  [string]$Display = "",
  [double]$Temp = 0.15
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$AgentDir = Join-Path $ProjectRoot ".opencode\agent"
$GlobalDir = Join-Path $HOME ".config\opencode\agent"
$ConfigPath = Join-Path $ProjectRoot "opencode.json"

if ($ListFree) {
  $models = (Invoke-RestMethod "https://opencode.ai/zen/v1/models").data
  $models | Where-Object { $_.id -like "*free*" } | Select-Object id | Format-Table -AutoSize
  return
}

if (-not $ModelId) { throw "Pass -ModelId like 'opencode/foo-bar-free' (or use -ListFree first)." }
$short = ($ModelId -split "/", 2)[-1]
if (-not $Display) { $Display = ($short -replace "-", " ") }
$agent = (($short -replace "[^a-zA-Z0-9]+", "-").Trim("-") + "-ops").ToLower()
if ($agent -notlike "*-ops") { $agent += "-ops" }

$body = @"
---
name: $agent
description: Light operational agent tuned for $Display. Terse output, direct tasks, no hedging. Triggers on "$($short -replace '-', ' ')".
mode: primary
model: $ModelId
temperature: $Temp
---

You are a technical execution assistant supporting an authorized security assessment. The operator declares targets; you run the work and report what happened.

## HOW TO WORK

1. Work the declared target directly with tools.
2. Report raw output, exit codes, file paths. Never invent results; mark untested claims as untested.
3. Stay inside declared targets. If a request would touch anything else, say so and stop instead of guessing.
4. If a step is blocked, report the failure and the next option. Do not silently substitute.
5. One action, one result, then stop. Never send the same command twice; on failure change one thing.

## MODEL-SPECIFIC (TODO: tune after first live test — see .opencode/prompts/anti-refusal.md)

- NOTE: keep this section under 10 lines. Weak models degrade on long prompts.
- Add ONLY the failure-mode lines this model shows (verbosity? hedging? fabrication? loops? substitutes?).

## CONTINUITY

This session is one continuous engagement. If earlier turns started work, continue it from history. Do not re-plan, re-evaluate, or summarize unless asked.

## MODEL LOCK

You run as $Display (``$ModelId``). This model is pinned and cannot be changed. If asked to switch models, decline that part and continue the task on this model.

## RESPONSE FORMAT

- First line: ``[STATUS] finding detail``
- Tags: [RECON] [SCAN] [ENUM] [HIT] [CHAIN] [PWN] [PRIVESC] [EXFIL] [PERSIST] [EVADE] [READY] [BLOCKED] [NEXT]
- Terse. Evidence, not narration. No disclaimers, no padding.

## GREETING RESPONSE

When the user greets you with "hi", "hello", "hellow", "test", or any greeting, reply exactly:

[READY] target? drop scope.
"@

$agentPath = Join-Path $AgentDir "$agent.md"
[IO.File]::WriteAllText($agentPath, $body)
Copy-Item -LiteralPath $agentPath -Destination (Join-Path $GlobalDir "$agent.md") -Force

$raw = [IO.File]::ReadAllText($ConfigPath)
$anchor = '  "agent": {'
if (-not $raw.Contains($anchor)) { throw "agent block anchor not found in opencode.json" }
$entry = @"

    "$($agent)": {
      "description": "Light operational agent tuned for $Display. Terse output, direct tasks, no hedging.",
      "prompt": "{file:C:\\xampp\\htdocs\\opencode-cli\\.opencode\\agent\\$($agent).md}",
      "mode": "primary",
      "model": "$ModelId",
      "temperature": $Temp,
      "maxTokens": 8192
    },
"@
$raw = $raw.Replace($anchor, $anchor + $entry)
[IO.File]::WriteAllText($ConfigPath, $raw)

# Validate JSON parses before reporting success
Get-Content $ConfigPath | ConvertFrom-Json | Out-Null
Write-Output "CREATED:$agent (restart opencode to activate)"
