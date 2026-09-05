# Renders each narration segment to its own WAV with the Windows speech synthesiser,
# and writes a manifest of the resulting durations.
#
# One file per segment (rather than one long track) is what lets the capture script
# hold each UI beat for exactly as long as its narration lasts, so the picture and the
# voice stay in step without hand-tuned sleeps.

param(
    [string]$Voice = "Microsoft Zira Desktop",
    [int]$Rate = 0,
    [string]$OutDir = "$PSScriptRoot\audio"
)

Add-Type -AssemblyName System.Speech

$segments = Get-Content "$PSScriptRoot\narration.json" -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$manifest = @()
foreach ($seg in $segments) {
    $path = Join-Path $OutDir "$($seg.id).wav"

    # A fresh synthesiser per file: SetOutputToWaveFile keeps the handle open until the
    # object is disposed, and reusing one leaves zero-byte files behind.
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synth.SelectVoice($Voice)
    } catch {
        Write-Warning "Voice '$Voice' unavailable; using the system default."
    }
    $synth.Rate = $Rate
    $synth.SetOutputToWaveFile($path)
    $synth.Speak($seg.text)
    $synth.Dispose()

    $reader = New-Object System.Media.SoundPlayer $path
    $reader.Load()
    $bytes = (Get-Item $path).Length
    # 16-bit mono PCM at 22.05 kHz is what SAPI writes here; 44 bytes of header.
    $seconds = [math]::Round(($bytes - 44) / (22050.0 * 2), 2)
    $reader.Dispose()

    $manifest += [PSCustomObject]@{ id = $seg.id; file = "$($seg.id).wav"; seconds = $seconds }
    "{0,-16} {1,6:N2}s" -f $seg.id, $seconds
}

$manifest | ConvertTo-Json -Depth 3 | Set-Content "$OutDir\manifest.json" -Encoding UTF8
""
"total: {0:N1}s ({1:N1} min) across {2} segments" -f (($manifest | Measure-Object seconds -Sum).Sum), (($manifest | Measure-Object seconds -Sum).Sum / 60), $manifest.Count
"manifest: $OutDir\manifest.json"
