import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { randomBytes } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [hqDir, file, marker, toolId] = process.argv.slice(2);
const load = (path) => import(pathToFileURL(join(hqDir, path)).href);
const { startHQ, hello } = await load('tests/hq-harness.mjs');
const { referenceBroker } = await load('tests/broker-fixture.mjs');
const { contracts } = await load('tests/support/member-client-contracts.mjs');
const { definePlugin } = await load('sdk/plugin.mjs');
const { fail } = await load('sdk/errors.mjs');
const { connectHq } = await load('sdk/local-socket-client.mjs');
const { abortable } = await load('sdk/broker-stream.mjs');
const { bindProof } = await load('sdk/member-session.mjs');

const usage = { input_tokens: 7, output_tokens: 3 };
let runtime, handle, producerCalls = 0, expectedTurn, continueText;
const provider = definePlugin({ name:'fixture-inference', version:'0.1.0', hostApi:1,
  requires:{'hq-admission':'^0.1.0'}, services:['budgets','store'],
  async init(ctx) {
    ctx.broker.registerInference({
      authorize: async (request, caller) => ctx.deps['hq-admission'].withBoundRuntime(caller.runtime_id, rt => {
        if (caller.runtime_id !== runtime.runtime_id || request.handle !== handle || rt.handle !== handle ||
            request.task_id !== 'task_fixture' || request.destination !== 'fixture-provider') fail('credential_unavailable','fixture grant required');
        return { soul:rt.soul_id, maxCost:0 };
      }),
      catalog: () => ({models:['claude-haiku-4-5-20251001']}),
      async *responses(request, _grant, signal) {
        assert.ok(isDeepStrictEqual(request.turn, expectedTurn), 'modeled turn changed in transport');
        const messages = request.turn.messages;
        assert.ok(Number.isSafeInteger(request.turn.max_output_tokens) && request.turn.max_output_tokens > 0);
        producerCalls++;
        if (messages.length === 1) {
          yield {type:'response.tool_call.start',call_id:toolId,name:'Read'};
          yield {type:'response.tool_call.arguments.delta',call_id:toolId,delta:JSON.stringify({file_path:file})};
          return {stop_reason:'tool_use',usage,actualCost:0};
        }
        const call = messages[1].content.find(block => block.type === 'tool_use');
        const result = messages[2].content.find(block => block.type === 'tool_result');
        assert.ok(call.id === toolId && result.tool_use_id === toolId, 'Read calls did not correlate');
        assert.ok(isDeepStrictEqual(call.input, { file_path: file }), 'Read targeted the wrong fixture');
        assert.ok(typeof result.content === 'string' && result.content.includes(marker), 'Read marker missing');
        yield { type: 'response.output_text.delta', delta: 'fixture-first-' + randomBytes(12).toString('hex') };
        await abortable(() => continueText.promise, AbortSignal.any([signal, AbortSignal.timeout(12_000)]));
        yield { type: 'response.output_text.delta', delta: 'fixture-last-' + randomBytes(12).toString('hex') };
        return {stop_reason:'end_turn',usage,actualCost:0};
      },
    });
  }
});
const t = await startHQ({plugins:[referenceBroker(),provider]});
const entry=await t.entry();
const admitted=await entry.request('Hello',hello());
runtime=admitted.result.admitted_runtimes[0];
const equipped=await entry.request('Equip',{runtime_id:runtime.runtime_id});
handle=equipped.result.grant_handles[0].handle;
t.hq.core.budgets.set(hello().soul_id,{ceilings:{working:0,maintenance:0},reserved:new Map()});
const client=await connectHq(t.hq.paths.broker,{contracts});
const ch=await client.request('bind.challenge',{runtime_id:runtime.runtime_id});
const bind=await client.request('bind',{runtime_id:runtime.runtime_id,proof:bindProof(runtime.channel_binding,ch.nonce,runtime.runtime_id)});
assert.equal(bind.runtime_id,runtime.runtime_id);
function emit(frame) {
  process.stdout.write(JSON.stringify(frame) + '\n');
}

async function read(lines) {
  const { done, value } = await lines.next();
  if (done) return undefined;
  assert.ok(Buffer.byteLength(value) <= 1 << 20, 'fixture request exceeded its byte limit');
  return JSON.parse(value);
}

function request(turn, operation, stop, onChunk) {
  assert.equal(turn?.messages?.length, operation === 1 ? 1 : 3, 'unexpected fixture turn');
  expectedTurn = structuredClone(turn);
  return client.requestResponses({
    handle, destination: 'fixture-provider', task_id: 'task_fixture',
    operation_id: `op_${operation}`, model: 'claude-haiku-4-5-20251001', turn,
  }, onChunk).then((result) => {
    assert.ok(isDeepStrictEqual(result, { stop_reason: stop, usage }), 'unexpected stop or usage');
    return result;
  });
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const lines = input[Symbol.asyncIterator]();
try {
  emit({ ready: true, profile: admitted.result.profile });
  const toolTurn = await read(lines);
  const toolResult = await request(toolTurn, 1, 'tool_use', (event) => emit({ event }));
  emit({ result: toolResult });

  continueText = Promise.withResolvers();
  const textTurn = await read(lines);
  let textChunks = 0;
  const textRequest = request(textTurn, 2, 'end_turn', (event) => {
    assert.equal(event.type, 'response.output_text.delta', 'expected a text delta');
    assert.ok(++textChunks <= 2, 'unexpected text delta');
    emit({ event });
  });
  const confirmation = await Promise.race([
    read(lines),
    textRequest.then(() => { throw new Error('terminal response preceded first-delivery confirmation'); }),
  ]);
  assert.ok(textChunks === 1 && isDeepStrictEqual(confirmation, { continue: true }),
    'first-delivery confirmation missing or out of order');
  continueText.resolve();
  const textResult = await textRequest;
  assert.equal(textChunks, 2);
  emit({ result: textResult });

  assert.ok((await lines.next()).done, 'unexpected input after the two fixture operations');
  assert.equal(producerCalls, 2);
  const rows = JSON.parse(readFileSync(join(t.home, 'store.json'), 'utf8')).tables
    .find(([name]) => name === '@hq\0inference_budget')?.[1] ?? [];
  assert.equal(rows.length, 2);
  assert.ok(rows.every(([, row]) => row.outcome === 'completed' && row.actual_cost === 0
    && isDeepStrictEqual(row.usage, usage)));
  process.stderr.write('PASS bound HQ inference: two completed zero-cost operations with measured usage\n');
} catch {
  process.stderr.write('FAIL bound HQ fixture exchange\n');
  process.exitCode = 1;
} finally {
  input.close();
  client.destroy();
  await t.done();
}
