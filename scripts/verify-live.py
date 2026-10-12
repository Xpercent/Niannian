"""Read-only verification of the running app using its actual persisted data."""

import argparse
import json
from urllib.parse import quote
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'

    def get(path):
        with urlopen(base + path, timeout=15) as response:
            return json.load(response)

    overview = get('/api/overview')
    assert overview['wechat']['state'] == 'connected', 'WeChat is not connected'
    assert overview['stats']['messages'] > 0, 'No real captured messages'
    assert get('/api/health')['thinking_enabled'] is False
    tasks = get('/api/tasks?status=all')
    source_links = 0
    for task in tasks:
        detail = get(f"/api/tasks/{task['id']}")
        assert detail['messages'], 'Task is missing original sources'
        assert set(detail['source_message_ids']) == {row['id'] for row in detail['messages']}
        assert all(row['session_id'] == detail['session_id'] for row in detail['messages'])
        assert detail['events'], 'Task is missing audit events'
        source_links += len(detail['messages'])

    seen = set()
    local_media = 0
    paginated_sessions = 0
    self_messages = 0
    for session in overview['sessions']:
        if not session['message_count']:
            continue
        cursor = None
        session_seen = set()
        while True:
            path = f"/api/sessions/{quote(session['id'], safe='')}/messages?limit=17"
            if cursor is not None:
                path += f'&before_id={cursor}'
            page = get(path)
            ids = {row['id'] for row in page['items']}
            assert not (ids & session_seen), 'Duplicate pagination row'
            session_seen.update(ids)
            for message in page['items']:
                if message['is_self']:
                    self_messages += 1
                filename = message['metadata'].get('local_file')
                if filename:
                    with urlopen(base + '/media/' + quote(filename, safe='/'), timeout=15) as response:
                        assert response.status == 200 and response.read(64), 'Blank local attachment'
                    local_media += 1
            if not page['has_more']:
                break
            cursor = page['before_id']
        assert len(session_seen) == session['message_count'], 'Pagination count mismatch'
        assert not (seen & session_seen), 'Cross-session duplicate IDs'
        seen.update(session_seen)
        paginated_sessions += 1

    exported = get('/api/export')
    assert len(exported['messages']) >= len(seen)
    assert len(exported['tasks']) == len(tasks)
    report = {'ok': True, 'messages_paginated': len(seen), 'sessions_paginated': paginated_sessions,
              'catalog_sessions': len(overview['sessions']), 'tasks_with_sources_and_events': len(tasks),
              'source_links': source_links, 'self_messages': self_messages,
              'local_attachments_renderable': local_media, 'queued': overview['stats']['queued'],
              'thinking_enabled': False}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
