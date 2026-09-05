/**
 * Drives the Moneta dashboard through the DEMO.md beats and records the result.
 *
 * Each beat holds for exactly as long as its narration segment lasts (read from
 * audio/manifest.json), so the finished video lines up with the voice track without
 * anyone hand-tuning sleeps. Add a segment to narration.json, regenerate the audio,
 * and the timing here follows automatically.
 *
 * The Ask beat is the one that talks to a live model, so it is given a generous
 * timeout and, if it fails, the run continues rather than losing the whole take —
 * the fallback shows the same case already investigated on the Exceptions tab.
 */

import { chromium } from "playwright"
import { readFileSync, mkdirSync, writeFileSync } from "node:fs"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const WEB = process.env.MONETA_WEB ?? "http://127.0.0.1:3000"
const OUT = join(HERE, "video")
const VIEWPORT = { width: 1600, height: 900 }

// PowerShell writes UTF-8 with a BOM, which JSON.parse rejects.
const manifest = JSON.parse(readFileSync(join(HERE, "audio", "manifest.json"), "utf8").replace(/^﻿/, ""))
const seconds = Object.fromEntries(manifest.map((m) => [m.id, m.seconds]))

/** The settlement whose refund the agent traced. Verified in out/holdout.findings.json. */
const CASE_UTR = "640771585351"
const ASK_QUESTION = `Why doesn't settlement ${CASE_UTR} match what we booked?`

const timeline = []
let elapsed = 0
// Wall-clock origin for the video track. Page loads, tab clicks and inter-beat
// glides consume real time that no beat accounts for, so narration must be placed
// by measured offset from the start of the recording, not by summing beat lengths.
let videoT0 = 0

async function beat(id, fn) {
  const dur = seconds[id]
  if (dur === undefined) throw new Error(`no narration segment '${id}'`)
  const started = Date.now()
  const videoAt = (started - videoT0) / 1000
  timeline.push({
    id,
    videoAt: Number(videoAt.toFixed(2)),
    startsAt: Number(elapsed.toFixed(2)),
    seconds: dur,
  })
  console.log(`  video ${String(videoAt.toFixed(1)).padStart(6)}s  ${id}  (${dur}s)`)
  if (fn) await fn()
  // Whatever the action consumed counts toward the beat; hold for the remainder so
  // the picture never runs ahead of the narration.
  const spent = (Date.now() - started) / 1000
  if (spent < dur) await sleep(dur - spent)
  elapsed += Math.max(dur, spent)
}

const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000))

/** Smooth scroll, because an instant jump reads as a glitch on video. */
async function glide(page, y, ms = 900) {
  await page.evaluate(
    ([target, duration]) =>
      new Promise((resolve) => {
        const start = window.scrollY
        const delta = target - start
        const t0 = performance.now()
        const step = (t) => {
          const p = Math.min((t - t0) / duration, 1)
          // easeInOutQuad
          const e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2
          window.scrollTo(0, start + delta * e)
          p < 1 ? requestAnimationFrame(step) : resolve()
        }
        requestAnimationFrame(step)
      }),
    [y, ms],
  )
}

const tab = (page, name) => page.getByRole("button", { name, exact: true }).first()

async function main() {
  mkdirSync(OUT, { recursive: true })
  // Uses the Chrome already installed on this machine rather than downloading a
  // Playwright build — the CDN download times out here, and the installed browser is
  // also what the demo is actually shown in.
  const browser = await chromium.launch({
    channel: "chrome",
    args: ["--force-device-scale-factor=1", "--hide-scrollbars"],
  })
  const context = await browser.newContext({
    viewport: VIEWPORT,
    recordVideo: { dir: OUT, size: VIEWPORT },
    deviceScaleFactor: 1,
  })
  const page = await context.newPage()
  videoT0 = Date.now()

  console.log("recording…")

  // ---- The problem: open on the landing page, which states it in one screen. ----
  await page.goto(WEB, { waitUntil: "networkidle" })
  await sleep(1.5)
  await beat("01-problem-a")
  await beat("02-problem-b", () => glide(page, 620))
  await beat("03-problem-c", () => glide(page, 1150))

  // ---- The honest numbers ----
  await page.goto(`${WEB}/dashboard`, { waitUntil: "networkidle" })
  await page.waitForSelector("text=Match rate by value", { timeout: 30_000 })
  await sleep(1)
  await beat("04-numbers-a")
  await beat("05-numbers-b")
  await beat("06-numbers-c", () => glide(page, 340))
  await beat("07-numbers-d", () => glide(page, 900))

  // ---- Rules first, LLM second ----
  await glide(page, 0, 500)
  await tab(page, "Exceptions").click()
  await page.waitForSelector("table", { timeout: 20_000 })
  await sleep(1)

  await beat("08-rules-a", async () => {
    const dup = page.locator("tr", { hasText: "Duplicate booking" }).first()
    if (await dup.count()) {
      await dup.click()
      await sleep(1)
      await glide(page, 260)
    }
  })
  await beat("09-rules-b")

  await beat("10-rules-c", async () => {
    await glide(page, 0, 400)
    const filter = page.getByPlaceholder("Filter by id or rule…")
    await filter.click()
    await filter.type("booked_bank_credit", { delay: 45 })
    await sleep(2)
  })

  // ---- The moment: ask it live ----
  await tab(page, "Ask Moneta").click()
  await sleep(1.5)

  let askFailed = false
  await beat("11-moment-a", async () => {
    const input = page.getByPlaceholder(/Why doesn't order/)
    await input.click()
    await input.type(ASK_QUESTION, { delay: 32 })
    await sleep(0.6)
    await page.getByRole("button", { name: "Ask", exact: true }).click()
  })

  await beat("12-moment-b", async () => {
    // The tool-call chips appear as the agent works; that is the shot.
    try {
      await page.waitForSelector("text=/Evidence gathered/", { timeout: 60_000 })
    } catch {
      askFailed = true
      console.warn("  ! the Ask beat did not return in time")
    }
  })

  await beat("13-moment-c", async () => {
    const trace = page.locator("text=/Evidence gathered/").first()
    if (await trace.count()) await trace.click()
    await sleep(1)
    await glide(page, 400)
  })
  await beat("14-moment-d")
  await beat("15-moment-e", () => glide(page, 800))

  // ---- Proving it ----
  await glide(page, 0, 400)
  await tab(page, "Evaluation").click()
  await page.waitForSelector("text=Detection recall", { timeout: 30_000 })
  await sleep(1)
  await beat("16-proof-a")
  await beat("17-proof-b")
  await beat("18-proof-c", () => glide(page, 420))
  await beat("19-proof-d", () => glide(page, 780))

  // ---- Audit trail ----
  await glide(page, 0, 400)
  await tab(page, "Audit trail").click()
  await page.waitForSelector("text=/Audit trail/", { timeout: 20_000 })
  await sleep(1)
  await beat("20-audit-a", async () => {
    const search = page.getByPlaceholder("Search events…")
    await search.click()
    await search.type("tool_call", { delay: 45 })
    await sleep(1.5)
  })
  await beat("21-audit-b", () => glide(page, 300))

  // ---- Close on the landing page's closing statement ----
  await page.goto(WEB, { waitUntil: "networkidle" })
  await beat("22-close-a", async () => {
    await glide(page, 4200, 1600)
  })
  await beat("23-close-b", async () => {
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await sleep(1)
  })

  await sleep(1.5)
  await context.close()
  await browser.close()

  writeFileSync(
    join(OUT, "timeline.json"),
    JSON.stringify({ viewport: VIEWPORT, askFailed, totalSeconds: elapsed, timeline }, null, 2),
  )
  console.log(`\ndone — ${elapsed.toFixed(1)}s`)
  if (askFailed) console.log("NOTE: the live Ask beat failed; check the take before publishing.")
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
