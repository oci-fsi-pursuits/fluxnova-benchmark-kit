"""Counted pagination for quiescent FNV-06 inventories; never retry mutations."""
from datetime import datetime, timezone
from urllib.parse import urlencode

from benchlib.fnv06_functional import Recorder, INVENTORIES


class PaginatedRecorder(Recorder):
    def collection(self, path, parameters=None):
        parameters = parameters or {}
        def query(base, extra=None):
            values = {**parameters, **(extra or {})}
            return base + ('?' + urlencode(values) if values else '')
        before = self.get(query(path + '/count')); total = before['response']['count']
        if type(total) is not int or not 0 <= total <= 100000:
            raise RuntimeError('Invalid or excessive inventory count')
        rows, pages = [], []
        for offset in range(0, total, 500):
            page = self.get(query(path, {'firstResult': offset, 'maxResults': 500}))
            if not isinstance(page['response'], list) or len(page['response']) != min(500, total-offset):
                raise RuntimeError('Incomplete or changing inventory page')
            pages.append(page['index']); rows.extend(page['response'])
        terminal = self.get(query(path, {'firstResult': total, 'maxResults': 500}))
        after = self.get(query(path + '/count'))
        if terminal['response'] != [] or after['response'] != {'count': total}:
            raise RuntimeError('Inventory changed or server ignored pagination')
        if len({r['id'] for r in rows}) != total:
            raise RuntimeError('Duplicated or missing inventory identity')
        return {'format': 'counted-pages-v1', 'path': path, 'parameters': parameters, 'page_size': 500,
            'rows': rows, 'count': total, 'count_before_request': before['index'],
            'list_requests': pages, 'terminal_request': terminal['index'], 'count_request': after['index'],
            'list_request': pages[0] if pages else terminal['index']}

    def snapshot(self):
        return {'at': datetime.now(timezone.utc).isoformat(),
                'inventories': {name: self.collection(path) for name,path in INVENTORIES.items()}}
