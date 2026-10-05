param(
    [switch]$Menu,
    [switch]$DryRun,
    [ValidateSet("hacker", "default", "mobile-application")]
    [string]$Persona,
    [Parameter(ValueFromRemainingArguments=$true)]
    [String[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

# Suppress Node/Bun runtime warnings (like MaxListenersExceededWarning from MCP getPrompt)
# from being printed to stderr, which corrupts the OpenCode TUI terminal buffer.
$env:NODE_NO_WARNINGS = "1"

# Decide whether to prompt. `-Menu` is forced by the Git Bash shim when stdin is a
# real terminal (MinTTY hands Windows console apps a pipe, so interactivity cannot
# be detected from here). For PowerShell/cmd launches we detect it ourselves. A
# non-interactive launch with no persona set keeps the previous fallback (hacker)
# so scripted `opencode run` calls are unaffected.
$interactive = $false
try { $interactive = -not [Console]::IsInputRedirected } catch { $interactive = $false }
# With pipeline input PowerShell will not hand stdin to Read-Host reliably - the
# prompt blocks forever. Never prompt in that case: scripts should pass -Persona,
# and an unattended pipe falls back to the default persona below.
$piped = $MyInvocation.ExpectingInput

if ($Persona) {
    # Explicit selection wins, so scripts and tests never have to answer the prompt.
    $env:CYBERSTRIKE_PERSONA = $Persona
} elseif (-not $piped) {
    Write-Host "Select CyberStrike Persona:"
    Write-Host "1) Hacker (anti-refusal wrapper ON, hacker agent injected)" -ForegroundColor Red
    Write-Host "2) Default (clean, stock opencode: no hooks, no hacker agent, no persona config)" -ForegroundColor Blue
    $choice = Read-Host "Choice [1]"
    if ($choice -eq '2') {
        $env:CYBERSTRIKE_PERSONA = "default"
    } else {
        $env:CYBERSTRIKE_PERSONA = "hacker"
    }
} elseif (-not $env:CYBERSTRIKE_PERSONA) {
    $env:CYBERSTRIKE_PERSONA = "hacker"
}

$isHacker = $env:CYBERSTRIKE_PERSONA -eq "hacker"

# A default session must be indistinguishable from a stock opencode install. `--pure`
# (added below) only stops *plugins*; two other external surfaces keep loading, and
# both of them are CyberStrike-specific here:
#   - skills auto-loaded from ~/.claude/skills and ~/.agents/skills
#   - CLAUDE.md merging from ~/.claude/CLAUDE.md
# Disable both for default. They are explicitly unset for hacker so a persona switch
# in the same shell cannot inherit the previous run's isolation.
if ($isHacker) {
    Remove-Item Env:OPENCODE_DISABLE_EXTERNAL_SKILLS -ErrorAction SilentlyContinue
    Remove-Item Env:OPENCODE_DISABLE_CLAUDE_CODE -ErrorAction SilentlyContinue
} else {
    $env:OPENCODE_DISABLE_EXTERNAL_SKILLS = "1"
    $env:OPENCODE_DISABLE_CLAUDE_CODE = "1"
}

# Config dir must be defined before any block that writes into it. The hacker
# block below (system-prompts.json copy + live opencode.json plugin merge) runs
# before the persona-swap section, so a late definition left $configDir empty
# there and the copy resolved to the drive root (C:\system-prompts.json), which
# a standard user cannot write.
$configDir = "$HOME\.config\opencode"

# Ensure the plugin directory exists
$pluginDir = "$HOME\.config\opencode\plugin"
if (-not (Test-Path $pluginDir)) {
    New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
}
# cyberstrike-persona.js consolidated into cyberstrike-unified.js — remove stale copy
if (Test-Path "$pluginDir\cyberstrike-persona.js") { Remove-Item "$pluginDir\cyberstrike-persona.js" -Force }

# Keep the folder the single source of truth for every plugin opencode loads:
# refresh the remaining CyberStrike plugin sources from it on each launch.
$srcPlugin = "C:\xampp\htdocs\opencode-cli\plugin"
# plugin/cyberstrike/index.js+skills.ts consolidated into cyberstrike-unified.js — clean up stale copies
if (Test-Path "$pluginDir\cyberstrike") { Remove-Item "$pluginDir\cyberstrike" -Recurse -Force }
New-Item -ItemType Directory -Force -Path "$pluginDir\anti-claude-refusals\.opencode\plugins" | Out-Null
Copy-Item "$srcPlugin\anti-claude-refusals\.opencode\plugins\anti-killswitch.ts" "$pluginDir\anti-claude-refusals\.opencode\plugins\" -Force

# Anti-refusal runtime: the unified plugin (refusal regex + provenance + killchain scope)
# and the model-specific system-prompts rules. Both live in the repo as single source of
# truth; deploy them on every launch so a regeneration can never silently revert them.
if ($isHacker) {
    Copy-Item "C:\xampp\htdocs\opencode-cli\.opencode\plugin\cyberstrike-unified.js" "$pluginDir\cyberstrike-unified.js" -Force
    Copy-Item "C:\xampp\htdocs\opencode-cli\.opencode\plugin\cyberstrike-model-specific.js" "$pluginDir\cyberstrike-model-specific.js" -Force
    Copy-Item "C:\xampp\htdocs\opencode-cli\.opencode\plugin\system-prompt-override.js" "$pluginDir\system-prompt-override.js" -Force
    Copy-Item "C:\xampp\htdocs\opencode-cli\.opencode\system-prompts.json" "$configDir\system-prompts.json" -Force

    # Ensure the live opencode.json plugin array references all active plugins.
    $liveJson = "$configDir\opencode.json"
    $unifiedRef = "file:///$($pluginDir -replace '\\','/')/cyberstrike-unified.js"
    $modelSpecificRef = "file:///$($pluginDir -replace '\\','/')/cyberstrike-model-specific.js"
    $sysPromptRef = "file:///$($pluginDir -replace '\\','/')/system-prompt-override.js"
    if (Test-Path $liveJson) {
        try {
            $cfg = Get-Content $liveJson -Raw | ConvertFrom-Json
            if (-not $cfg.plugin) { $cfg | Add-Member -NotePropertyName plugin -NotePropertyValue @() }
            if ($cfg.plugin -notcontains $unifiedRef) { $cfg.plugin += $unifiedRef }
            if ($cfg.plugin -notcontains $modelSpecificRef) { $cfg.plugin += $modelSpecificRef }
            if ($cfg.plugin -notcontains $sysPromptRef) { $cfg.plugin += $sysPromptRef }
            $cfg | ConvertTo-Json -Depth 32 | Set-Content $liveJson -Encoding UTF8
        } catch {
            Write-Warning "opencode.ps1: could not ensure plugins in live config: $($_.Exception.Message)"
        }
    }
}

# Manage the hacker agent based on persona
$agentDir = "C:\cyberstrike\.opencode\agent"
$globalAgentDir = "$HOME\.config\opencode\agent"
if (-not (Test-Path $agentDir)) { New-Item -ItemType Directory -Force -Path $agentDir | Out-Null }
if (-not (Test-Path $globalAgentDir)) { New-Item -ItemType Directory -Force -Path $globalAgentDir | Out-Null }

$backupHacker = "C:\xampp\htdocs\opencode-cli\agent\hacker.md"
$localHacker = "$agentDir\hacker.md"
$globalHacker = "$globalAgentDir\hacker.md"

if ($isHacker) {
    if (Test-Path $backupHacker) {
        Copy-Item $backupHacker $localHacker -Force
        Copy-Item $backupHacker $globalHacker -Force
    }
} else {
    if (Test-Path $localHacker) { Remove-Item $localHacker -Force }
    if (Test-Path $globalHacker) { Remove-Item $globalHacker -Force }
}

# Swap the live opencode config overlay for the chosen persona. The always-loaded
# ~/.config/opencode/opencode.json keeps plugins/providers; the persona-specific
# keys (default_agent, instructions, model) live here so the default persona never
# inherits the hacker agent or the hacker-persona.md instructions file.
# ($configDir is defined near the top - see the plugin-install section.)
$liveConfig = "$configDir\opencode.jsonc"
$personaConfig = if ($isHacker) {
    "C:\xampp\htdocs\opencode-cli\persona\opencode.hacker.jsonc"
} else {
    "C:\xampp\htdocs\opencode-cli\persona\opencode.default.jsonc"
}
if (-not (Test-Path $configDir)) { New-Item -ItemType Directory -Force -Path $configDir | Out-Null }
Copy-Item $personaConfig $liveConfig -Force

# Build the opencode argument list. The default persona runs --pure (no external
# plugins at all), so it behaves exactly like a stock opencode install.
$opencodeArgs = @()
if (-not $isHacker) { $opencodeArgs += "--pure" }
if ($RemainingArgs) { $opencodeArgs += $RemainingArgs }

# Run the real opencode executable
$npmDir = "$env:APPDATA\npm"
$opencodeExe = "$npmDir\node_modules\opencode-ai\bin\opencode.exe"

if (-not (Test-Path $opencodeExe)) {
    Write-Error "Could not find opencode-ai executable at $opencodeExe"
    exit 1
}

if ($DryRun) {
    Write-Host ""
    Write-Host "persona        : $($env:CYBERSTRIKE_PERSONA)"
    Write-Host "config overlay : $personaConfig"
    Write-Host "config live    : $liveConfig"
    Write-Host "persona plugin : $pluginDir\cyberstrike-persona.js"
    Write-Host "hacker agent   : $(if (Test-Path $localHacker) { $localHacker } else { '(removed)' })"
    Write-Host "isolation      : $(if ($isHacker) { '(none)' } else { '--pure + OPENCODE_DISABLE_EXTERNAL_SKILLS + OPENCODE_DISABLE_CLAUDE_CODE' })"
    Write-Host "command        : `"$opencodeExe`" $($opencodeArgs -join ' ')"
    exit 0
}

if ($MyInvocation.ExpectingInput) {
    $input | & $opencodeExe $opencodeArgs
} else {
    & $opencodeExe $opencodeArgs
}
exit $LASTEXITCODE
