import assert from "node:assert/strict";
import test from "node:test";
import { settleRunCleanup } from "../src/web-run-cleanup.js";

test("web run cleanup preserves a completed run when cleanup rejects asynchronously", async () => {
  const errors: unknown[] = [];
  const failure = new Error("Operation timed out after 12000ms");
  const completion = Promise.resolve("run completed").finally(() => settleRunCleanup(
    () => new Promise<void>((_resolve, reject) => setImmediate(() => reject(failure))),
    (error) => errors.push(error)
  ));
  assert.equal(await completion, "run completed");
  assert.deepEqual(errors, [failure]);
});

test("web run cleanup handles synchronous failures and successful cleanup", async () => {
  const errors: unknown[] = [];
  const failure = new Error("close failed");
  await settleRunCleanup(() => { throw failure; }, (error) => errors.push(error));
  await settleRunCleanup(async () => undefined, (error) => errors.push(error));
  assert.deepEqual(errors, [failure]);
});
