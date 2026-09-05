/**
 * Lays the narration segments onto the recorded video at their measured offsets and
 * encodes the result to MP4.
 *
 * The narration track is assembled here rather than in an ffmpeg filter graph: each
 * segment is copied into a silent PCM buffer at the `videoAt` timestamp the capture
 * run recorded. Placing by measured offset (rather than concatenating clips back to
 * back) is what keeps the voice aligned through the page loads and tab switches that
 * happen between beats, and writing the samples directly avoids `amix`, whose
 * level-normalisation options vary across ffmpeg versions.
 */

import { execFileSync } from "node:child_process"
import { readdirSync, readFileSync, writeFileSync, existsSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const FFMPEG = join(HERE, "node_modules", "@ffmpeg-installer", "win32-x64", "ffmpeg.exe")
const VIDEO_DIR = join(HERE, "video")
const AUDIO_DIR = join(HERE, "audio")
const TRACK = join(HERE, "narration.wav")
const OUT = join(HERE, "moneta-demo.mp4")
const TAIL_SECONDS = 1.0

const readJson = (p) => JSON.parse(readFileSync(p, "utf8").replace(/^﻿/, ""))

/** Minimal RIFF reader — enough for the PCM files SAPI writes. */
function readWav(path) {
  const buf = readFileSync(path)
  if (buf.toString("ascii", 0, 4) !== "RIFF" || buf.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error(`${path} is not a RIFF/WAVE file`)
  }
  let fmt = null
  let data = null
  // Chunks are not guaranteed to be in a fixed order or tightly packed, so walk them.
  for (let pos = 12; pos + 8 <= buf.length; ) {
    const id = buf.toString("ascii", pos, pos + 4)
    const size = buf.readUInt32LE(pos + 4)
    const body = pos + 8
    if (id === "fmt ") {
      fmt = {
        format: buf.readUInt16LE(body),
        channels: buf.readUInt16LE(body + 2),
        sampleRate: buf.readUInt32LE(body + 4),
        bits: buf.readUInt16LE(body + 14),
      }
    } else if (id === "data") {
      data = buf.subarray(body, Math.min(body + size, buf.length))
    }
    pos = body + size + (size % 2) // chunks are word-aligned
  }
  if (!fmt || !data) throw new Error(`${path} is missing a fmt or data chunk`)
  if (fmt.format !== 1 || fmt.bits !== 16) {
    throw new Error(`${path}: expected 16-bit PCM, got format ${fmt.format} / ${fmt.bits}-bit`)
  }
  return { ...fmt, data }
}

function writeWav(path, { sampleRate, channels, data }) {
  const header = Buffer.alloc(44)
  const byteRate = sampleRate * channels * 2
  header.write("RIFF", 0, "ascii")
  header.writeUInt32LE(36 + data.length, 4)
  header.write("WAVE", 8, "ascii")
  header.write("fmt ", 12, "ascii")
  header.writeUInt32LE(16, 16) // PCM fmt chunk size
  header.writeUInt16LE(1, 20) // PCM
  header.writeUInt16LE(channels, 22)
  header.writeUInt32LE(sampleRate, 24)
  header.writeUInt32LE(byteRate, 28)
  header.writeUInt16LE(channels * 2, 32) // block align
  header.writeUInt16LE(16, 34)
  header.write("data", 36, "ascii")
  header.writeUInt32LE(data.length, 40)
  writeFileSync(path, Buffer.concat([header, data]))
}

const webm = readdirSync(VIDEO_DIR).find((f) => f.endsWith(".webm"))
if (!webm) throw new Error("no recording in video/ — run capture.mjs first")
const { timeline, askFailed } = readJson(join(VIDEO_DIR, "timeline.json"))

if (askFailed) {
  console.warn("WARNING: the live Ask beat failed in this take. Check it before publishing.\n")
}

const clips = timeline.map((t) => {
  const file = join(AUDIO_DIR, `${t.id}.wav`)
  if (!existsSync(file)) throw new Error(`missing narration audio: ${t.id}.wav`)
  return { ...t, wav: readWav(file) }
})

const { sampleRate, channels } = clips[0].wav
for (const c of clips) {
  if (c.wav.sampleRate !== sampleRate || c.wav.channels !== channels) {
    throw new Error(`${c.id} has a different audio format to the rest of the narration`)
  }
}

const last = clips[clips.length - 1]
const totalSeconds = last.videoAt + last.wav.data.length / (sampleRate * channels * 2) + TAIL_SECONDS
const frameBytes = channels * 2
const track = Buffer.alloc(Math.ceil(totalSeconds * sampleRate) * frameBytes) // silence

let overlaps = 0
let cursor = 0
for (const c of clips) {
  const offset = Math.round(c.videoAt * sampleRate) * frameBytes
  // A frame or two of rounding is not an overlap worth reporting; 50 ms is.
  if (offset < cursor - sampleRate * frameBytes * 0.05) overlaps++
  c.wav.data.copy(track, offset)
  cursor = offset + c.wav.data.length
}
if (overlaps) console.warn(`WARNING: ${overlaps} narration segment(s) overlap the previous one`)

writeWav(TRACK, { sampleRate, channels, data: track })
console.log(`narration track: ${(totalSeconds / 60).toFixed(2)} min at ${sampleRate} Hz`)

const args = [
  "-y",
  "-i", join(VIDEO_DIR, webm),
  "-i", TRACK,
  "-map", "0:v",
  "-map", "1:a",
  "-c:v", "libx264",
  "-crf", "20",
  "-preset", "medium",
  "-pix_fmt", "yuv420p",   // required by QuickTime and most browsers
  "-movflags", "+faststart",
  "-c:a", "aac",
  "-b:a", "160k",
  "-ar", "44100",
  OUT,
]

console.log("encoding…")
try {
  execFileSync(FFMPEG, args, { stdio: ["ignore", "ignore", "pipe"], maxBuffer: 1 << 26 })
} catch (err) {
  process.stderr.write(String(err.stderr ?? "").slice(-3000))
  process.exit(1)
}
console.log(OUT)
