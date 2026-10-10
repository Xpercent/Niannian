// Critical presentation boundaries only; no message fixtures or database writes.
import assert from 'node:assert/strict';
import { safeMedia, dateText, localInputTime } from '../niannian/web/src/lib/utils.ts';

assert.equal(safeMedia('account/附件 name.png'), '/media/account/%E9%99%84%E4%BB%B6%20name.png');
assert.equal(safeMedia('account\\photo.png'), '/media/account/photo.png');
assert.equal(safeMedia('/media/account/photo.png'), '/media/account/photo.png');
for (const value of ['../secret', 'account/../../secret', 'account/./photo', 'C:\\secret', '//remote/file', '/absolute/file', 'account//photo']) {
  assert.equal(safeMedia(value), '');
}
console.log('PASS attachment path boundary');
assert.equal(localInputTime('2026-10-08T18:30:00Z'), '2026-10-09T02:30');
assert.match(dateText('2026-10-08T18:30:00Z', true), /10\/09.*02:30/);
assert.equal(new Date(localInputTime('2026-10-08T18:30:00Z') + '+08:00').toISOString(), '2026-10-08T18:30:00.000Z');
console.log('PASS Shanghai deadline roundtrip');
for (const value of [null, undefined, '', 'not-a-date']) {
  assert.equal(localInputTime(value), '');
  assert.equal(dateText(value), '未设置');
}
console.log('PASS absent or invalid deadlines');
