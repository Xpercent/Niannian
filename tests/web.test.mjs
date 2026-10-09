// Pure presentation checks only: no invented WeChat messages or database writes.
import assert from 'node:assert/strict';
import {escape, safeMedia, date, localDateTime, options, dueText} from '../shiguang/web/helpers.js';

let checked = 0;
function check(name, fn) { fn(); checked++; console.log(`PASS ${name}`); }

check('untrusted text cannot create HTML elements or attributes', () => {
  assert.equal(escape('<script>"&\'</script>'), '&lt;script&gt;&quot;&amp;&#39;&lt;/script&gt;');
  assert.equal(escape(null), '');
});
check('attachment paths keep their account boundary and encode each segment', () => {
  assert.equal(safeMedia('account/附件 name.png'), '/media/account/%E9%99%84%E4%BB%B6%20name.png');
  assert.equal(safeMedia('account\\photo.png'), '/media/account/photo.png');
  assert.equal(safeMedia('/media/account/photo.png'), '/media/account/photo.png');
  for (const path of ['../secret', 'account/../../secret', 'account/./photo', 'C:\\secret', '//remote/file', '/absolute/file', 'account//photo']) assert.equal(safeMedia(path), '');
});
check('calendar fields use Shanghai time across UTC day boundaries', () => {
  assert.equal(localDateTime('2026-10-08T18:30:00Z'), '2026-10-09T02:30');
  assert.match(date('2026-10-08T18:30:00Z', true), /10\/09.*02:30/);
  assert.equal(new Date(localDateTime('2026-10-08T18:30:00Z') + '+08:00').toISOString(), '2026-10-08T18:30:00.000Z');
});
check('invalid and absent deadlines stay absent', () => {
  for (const value of [null, undefined, '', 'not-a-date']) { assert.equal(localDateTime(value), ''); assert.equal(dueText(value), ''); }
});
check('dynamic select labels and values cannot escape their context', () => {
  const html = options([['" autofocus="', '<svg onload=alert(1)>']], '');
  assert.ok(html.includes('value="&quot; autofocus=&quot;"'));
  assert.ok(html.includes('&lt;svg onload=alert(1)&gt;'));
  assert.ok(!html.includes('<svg'));
});

console.log(`Frontend helper checks: ${checked} passed.`);
