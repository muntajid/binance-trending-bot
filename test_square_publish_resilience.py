"""Offline regression tests for resilient Binance Square publishing.

Covers the two layers that keep a scheduled post alive when Binance's image
pipeline is slow: the ``lib.mjs`` poll-budget patch and the retry/fallback logic
in :mod:`bot.square_publisher`.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import patch_square_upload, square_publisher

# Mirrors the shape of the official square-post helper (the markers the patch
# relies on are byte-identical to upstream).
OFFICIAL_LIB = r'''const BASE_URL_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi";
const POLL_INTERVAL_MS = 3000;
const MAX_POLL_RETRIES = 10;

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function api(endpoint, apiKey, body, baseUrl = BASE_URL_V2) {
  const res = await fetch(`${baseUrl}${endpoint}`, {
    method: "POST",
    headers: {
      "X-Square-OpenAPI-Key": apiKey,
      "Content-Type": "application/json",
      clienttype: "binanceSkill",
    },
    body: JSON.stringify(body),
  });
  const raw = await res.text();
  const json = JSON.parse(raw);
  if (json.code !== "000000") {
    throw new Error(`API error [${json.code}]: ${json.message}`);
  }
  return json.data;
}

export async function pollImageStatus(apiKey, fileTicket) {
  for (let i = 0; i < MAX_POLL_RETRIES; i++) {
    const data = await api("/image/imageStatus", apiKey, { fileTicket });
    if (data.status === 1) return data;
    if (data.status === 2) throw new Error(`Processing failed: ${data.failedReason}`);
    console.log(`  Processing... (${i + 1}/${MAX_POLL_RETRIES})`);
    await sleep(POLL_INTERVAL_MS);
  }
  throw new Error(`Poll timed out after ${MAX_POLL_RETRIES} retries`);
}

export async function publish(apiKey, body) {
  return await api("/content/add", apiKey, body);
}
'''

POLL_DRIVER = r'''import { pollImageStatus } from "./lib.mjs";

const outcomes = [
  { status: 0 },
  { status: "0" },
  { status: "1", imageUrl: "https://img/binance.jpg" },
];
let calls = 0;
let sawSignal = false;

globalThis.fetch = async (url, options) => {
  sawSignal = options?.signal instanceof AbortSignal;
  const data = outcomes[Math.min(calls, outcomes.length - 1)];
  calls += 1;
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    text: async () => JSON.stringify({ code: "000000", data }),
  };
};

const result = await pollImageStatus("key", "ticket");
if (!result.imageUrl.includes("binance")) {
  throw new Error(`unexpected result: ${JSON.stringify(result)}`);
}
if (!sawSignal) {
  throw new Error("fetch was called without an AbortSignal timeout");
}
console.log("POLL_OK");
'''

TIMEOUT_DRIVER = r'''import { pollImageStatus } from "./lib.mjs";

let calls = 0;
globalThis.fetch = async () => {
  calls += 1;
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    text: async () => JSON.stringify({ code: "000000", data: { status: 0 } }),
  };
};

try {
  await pollImageStatus("key", "ticket");
  throw new Error("expected the poll to time out");
} catch (error) {
  if (!error.message.includes("Poll timed out after 2 retries")) {
    throw error;
  }
  if (calls !== 2) {
    throw new Error(`expected 2 polls, got ${calls}`);
  }
}
console.log("TIMEOUT_OK");
'''


class PatchSquareUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = self.tmp / "lib.mjs"
        self.lib.write_text(OFFICIAL_LIB, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_poll_budget_becomes_configurable_and_larger(self) -> None:
        warnings = patch_square_upload.patch_library(self.lib)
        patched = self.lib.read_text(encoding="utf-8")

        self.assertEqual(warnings, [])
        self.assertIn("positiveNumber(process.env.SQUARE_POLL_INTERVAL_MS, 4000)", patched)
        self.assertIn("positiveNumber(process.env.SQUARE_POLL_MAX_RETRIES, 20)", patched)
        self.assertIn("signal: AbortSignal.timeout(API_TIMEOUT_MS)", patched)
        self.assertNotIn("const MAX_POLL_RETRIES = 10;", patched)

    def test_patch_is_idempotent(self) -> None:
        patch_square_upload.patch_library(self.lib)
        once = self.lib.read_text(encoding="utf-8")
        warnings = patch_square_upload.patch_library(self.lib)
        twice = self.lib.read_text(encoding="utf-8")

        self.assertEqual(warnings, [])
        self.assertEqual(once, twice)
        self.assertEqual(twice.count("function positiveNumber("), 1)

    def test_refactored_helper_only_warns(self) -> None:
        self.lib.write_text("export async function somethingElse() {}\n", encoding="utf-8")

        warnings = patch_square_upload.patch_library(self.lib)

        self.assertTrue(warnings)
        self.assertTrue(any("marker not found" in warning for warning in warnings))

    @unittest.skipUnless(shutil.which("node"), "node is required for the helper smoke test")
    def test_patched_helper_polls_until_ready(self) -> None:
        patch_square_upload.patch_library(self.lib)
        (self.tmp / "poll.mjs").write_text(POLL_DRIVER, encoding="utf-8")

        completed = subprocess.run(
            [shutil.which("node"), "poll.mjs"],
            cwd=str(self.tmp),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "SQUARE_POLL_INTERVAL_MS": "5"},
        )

        self.assertIn("POLL_OK", completed.stdout, completed.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is required for the helper smoke test")
    def test_patched_helper_honours_small_budget(self) -> None:
        patch_square_upload.patch_library(self.lib)
        (self.tmp / "timeout.mjs").write_text(TIMEOUT_DRIVER, encoding="utf-8")

        env = {**os.environ, "SQUARE_POLL_MAX_RETRIES": "2", "SQUARE_POLL_INTERVAL_MS": "5"}
        completed = subprocess.run(
            [shutil.which("node"), "timeout.mjs"],
            cwd=str(self.tmp),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        self.assertIn("TIMEOUT_OK", completed.stdout, completed.stderr)


class FakeRunner:
    """Stand-in for the official node scripts; replays canned results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.timeouts = []

    def __call__(self, command, timeout=None):
        self.calls.append(list(command))
        self.timeouts.append(timeout)
        index = min(len(self.calls) - 1, len(self.results) - 1)
        return self.results[index]


class SquarePublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.image = self.tmp / "MEME_CARD.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.scripts = {}
        for name in ("post-image.mjs", "post-text.mjs"):
            script = self.tmp / name
            script.write_text("#!/usr/bin/env node\n", encoding="utf-8")
            self.scripts[name] = script
        self.patcher = [
            patch.object(square_publisher, "POST_IMAGE_SCRIPT", self.scripts["post-image.mjs"]),
            patch.object(square_publisher, "POST_TEXT_SCRIPT", self.scripts["post-text.mjs"]),
        ]
        for handle in self.patcher:
            handle.start()
        self.addCleanup(lambda: [handle.stop() for handle in self.patcher])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _publish(self, runner, images=("<image>",), **kwargs):
        options = {"backoff_seconds": (20, 40), "deadline_seconds": 600, "runner": runner}
        options.update(kwargs)
        slept = []
        options.setdefault("sleep", slept.append)
        resolved = [str(self.image)] if images == ("<image>",) else list(images or [])
        outcome = square_publisher.publish_to_square(
            "Trader Diary: 0.4% up.\n\n$BTC $ETH #BinanceSquare",
            resolved,
            **options,
        )
        return outcome, slept

    def test_media_stage_failures_are_retried(self) -> None:
        runner = FakeRunner(
            [
                (1, "Uploading: MEME_CARD.png\n  Processing... (1/20)\nFailed: Poll timed out after 20 retries"),
                (1, "Failed: Poll timed out after 20 retries"),
                (0, "Publishing...\nSuccess!\nID: 123"),
            ]
        )

        outcome, slept = self._publish(runner)

        self.assertEqual(outcome.mode, "image")
        self.assertTrue(outcome.published)
        self.assertTrue(outcome.degraded)
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(slept, [20.0, 40.0])
        self.assertIn("post-image.mjs", " ".join(runner.calls[0]))
        self.assertTrue(all(value > 0 for value in runner.timeouts))

    def test_text_fallback_keeps_the_slot_alive(self) -> None:
        runner = FakeRunner(
            [
                (1, "Failed: Poll timed out after 20 retries"),
                (1, "Failed: Poll timed out after 20 retries"),
                (0, "Publishing...\nSuccess!\nID: 456"),
            ]
        )

        outcome, _ = self._publish(runner, attempts=2)

        self.assertEqual(outcome.mode, "text")
        self.assertTrue(outcome.published)
        self.assertTrue(outcome.degraded)
        self.assertEqual(len(runner.calls), 3)  # 2 image attempts + 1 text post
        self.assertIn("post-text.mjs", " ".join(runner.calls[-1]))

    def test_fallback_can_be_disabled(self) -> None:
        runner = FakeRunner([(1, "Failed: Poll timed out after 20 retries")])

        with self.assertRaises(square_publisher.PublishError):
            self._publish(runner, attempts=2, fallback_to_text=False)

        self.assertEqual(len(runner.calls), 2)

    def test_no_retry_once_the_post_request_was_sent(self) -> None:
        runner = FakeRunner([(1, "Publishing...\nAPI error [-1]: internal")])

        with self.assertRaises(square_publisher.PublishError) as raised:
            self._publish(runner)

        self.assertIn("duplicate post", str(raised.exception))
        self.assertEqual(len(runner.calls), 1)

    def test_deadline_stops_further_image_attempts(self) -> None:
        runner = FakeRunner(
            [(1, "Failed: Poll timed out after 20 retries"), (0, "Publishing...\nSuccess!")]
        )
        ticks = [0, 10_000]

        def clock(ticks=ticks):
            return ticks.pop(0) if len(ticks) > 1 else ticks[-1]

        outcome, _ = self._publish(runner, deadline_seconds=1, clock=clock)

        self.assertEqual(outcome.mode, "text")
        self.assertEqual(len(runner.calls), 2)  # one image attempt, then the fallback

    def test_text_only_post_without_images(self) -> None:
        runner = FakeRunner([(0, "Publishing...\nSuccess!")])

        outcome, _ = self._publish(runner, images=[])

        self.assertEqual(outcome.mode, "text")
        self.assertFalse(outcome.degraded)
        self.assertIn("post-text.mjs", " ".join(runner.calls[0]))

    def test_classify_attempt(self) -> None:
        self.assertEqual(square_publisher.classify_attempt(0, "Success!"), "published")
        self.assertEqual(
            square_publisher.classify_attempt(1, "Failed: Poll timed out after 20 retries"),
            "retryable",
        )
        self.assertEqual(
            square_publisher.classify_attempt(1, "Publishing...\nfetch error"),
            "unknown",
        )

    def test_images_are_capped_and_missing_files_skipped(self) -> None:
        extra = [self.image for _ in range(6)]
        resolved = square_publisher.resolve_images(
            [str(self.image), *extra, str(self.tmp / "missing.png")]
        )

        self.assertEqual(len(resolved), square_publisher.MAX_IMAGES)
        self.assertTrue(all(Path(path).is_file() for path in resolved))


if __name__ == "__main__":
    unittest.main()
