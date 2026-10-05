"""Immutable local novel versions; SQL transactions keep originals and indexes together."""
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.ip_parser import chapter_index


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class LibraryError(ValueError):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class IPLibrary:
    def __init__(self, directory):
        self.path = Path(directory) / 'library.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, ip_id TEXT NOT NULL REFERENCES items(id), payload TEXT NOT NULL, text TEXT NOT NULL, original BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS versions_ip ON versions(ip_id);
                CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, hash TEXT NOT NULL, ip_id TEXT NOT NULL);''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _item(self, db, ip_id):
        row = db.execute('SELECT payload FROM items WHERE id=?', (ip_id,)).fetchone()
        if not row:
            raise LibraryError('IP_NOT_FOUND', '找不到此 IP 条目', 404)
        return json.loads(row[0])

    def version(self, ip_id, version_id, db=None):
        if db is None:
            with self.connection() as conn:
                return self.version(ip_id, version_id, conn)
        row = db.execute('SELECT payload, text FROM versions WHERE id=? AND ip_id=?', (version_id, ip_id)).fetchone()
        if not row:
            raise LibraryError('VERSION_NOT_FOUND', '找不到此正文版本', 404)
        return {**json.loads(row[0]), 'text': row[1]}

    def summary(self, item, versions):
        latest = versions[0] if versions else None
        reason = ('尚未导入正文' if not latest else '正文范围待确认' if latest['content_scope'] == 'unknown' else
                  '使用依据待核对' if item['rights_status'] == 'pending' or not item['rights_basis'] or not item['rights_scope'] else '')
        return {**item, 'version_count': len(versions), 'characters': latest['characters'] if latest else 0,
                'chapter_count': len(latest['chapters']) if latest else 0,
                'content_scope': latest['content_scope'] if latest else 'unknown',
                'availability': ('missing' if not latest else 'restricted') if reason else 'ready', 'restriction': reason}

    def detail(self, ip_id):
        with self.connection() as db:
            db.execute('BEGIN')
            item = self._item(db, ip_id)
            versions = [json.loads(r[0]) for r in db.execute('SELECT payload FROM versions WHERE ip_id=? ORDER BY rowid DESC', (ip_id,))]
        return {'item': self.summary(item, versions), 'versions': versions, 'projects': []}

    def list(self, query='', status='', page=1, genre=''):
        with self.connection() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM items ORDER BY rowid DESC')]
        items = [self.detail(ip_id)['item'] for ip_id in ids]
        # Stable sort keeps the original order within featured / ordinary works.
        items.sort(key=lambda item: not item.get('featured', False))
        # Keep options stable while searching/paging; types cover the whole library.
        preferred = ('都市', '玄幻', '东方玄幻', '仙侠', '历史', '武侠', '科幻', '悬疑推理',
                     '悬疑', '原创推理', '现代言情', '青春校园', '女生仙侠', '女生玄幻',
                     '女生悬疑', '古代言情', '女性江湖')
        genres = sorted({i['genre'] for i in items if i['genre']},
                        key=lambda g: (preferred.index(g) if g in preferred else len(preferred), g))
        words = query.casefold().split()
        items = [i for i in items if (not status or i['availability'] == status)
                 and (not genre or i['genre'] == genre)
                 and all(w in ' '.join(i[k] for k in ('title','author','genre','synopsis')).casefold() for w in words)]
        return {'items': items[(page-1)*24:page*24], 'total': len(items), 'genres': genres}

    def _check_revision(self, item, revision):
        if item['revision'] != revision:
            raise LibraryError('IP_CHANGED', '资料已在其他页面更新，请刷新后重新操作')

    def _save_item(self, db, item):
        db.execute('INSERT INTO items VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                   (item['id'], json.dumps(item, ensure_ascii=False)))

    def import_catalog(self, records):
        """Insert a validated display snapshot atomically; reruns never overwrite edits."""
        created = 0
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            # List uses newest first, so insert in reverse source order.
            for record in sorted(records, key=lambda r: r['catalog']['position'], reverse=True):
                catalog = record['catalog']
                ip_id = uuid.uuid5(uuid.NAMESPACE_URL, f"catalog-ip:{catalog['source_id']}").hex
                if db.execute('SELECT 1 FROM items WHERE id=?', (ip_id,)).fetchone():
                    continue
                item = {k:record.get(k, '') for k in ('title', 'author', 'genre', 'synopsis')}
                item.update(id=ip_id, revision=1, created_at=now(), updated_at=now(), latest_version=None,
                            rights_status='pending', rights_basis='', rights_scope='', catalog=catalog)
                self._save_item(db, item)
                created += 1
        return {'created': created, 'skipped': len(records)-created}

    def _replay(self, db, key, request_hash):
        row = db.execute('SELECT hash, ip_id FROM requests WHERE id=?', (key,)).fetchone()
        if row:
            if row[0] != request_hash:
                raise LibraryError('IDEMPOTENCY_CONFLICT', '此操作标识已用于其他内容，请刷新后重新操作')
            return row[1]

    def _add_version(self, db, item, parsed, raw, filename, scope):
        version_id = uuid.uuid4().hex
        count = db.execute('SELECT COUNT(*) FROM versions WHERE ip_id=?', (item['id'],)).fetchone()[0]
        payload = {k:v for k,v in parsed.items() if k != 'text'}
        payload.update(id=version_id, number=count+1, created_at=now(), filename=filename,
                       sha256=hashlib.sha256(raw).hexdigest(), text_sha256=hashlib.sha256(parsed['text'].encode()).hexdigest(), content_scope=scope)
        db.execute('INSERT INTO versions VALUES (?,?,?,?,?)', (version_id, item['id'], json.dumps(payload, ensure_ascii=False), parsed['text'], raw))
        item['latest_version'] = version_id
        return version_id

    def import_file(self, metadata, parsed, raw, filename, key, ip_id=None, revision=None):
        request_hash = digest({'metadata':metadata, 'raw':hashlib.sha256(raw).hexdigest(), 'filename':filename, 'ip_id':ip_id, 'revision':revision})
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            replay = self._replay(db, key, request_hash)
            if replay:
                return replay, False
            if ip_id:
                item = self._item(db, ip_id)
                self._check_revision(item, revision)
                item['revision'] += 1
                item['updated_at'] = now()
            else:
                item = {k:v for k,v in metadata.items() if k != 'content_scope'}
                item.update(id=uuid.uuid4().hex, revision=1, created_at=now(), updated_at=now(), latest_version=None)
                self._save_item(db, item)
            if parsed:
                self._add_version(db, item, parsed, raw, filename, metadata['content_scope'])
            self._save_item(db, item)
            db.execute('INSERT INTO requests VALUES (?,?,?)', (key, request_hash, item['id']))
        return item['id'], True

    def update(self, ip_id, metadata, revision):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            item = self._item(db, ip_id)
            self._check_revision(item, revision)
            item.update(metadata, revision=revision+1, updated_at=now())
            self._save_item(db, item)

    def set_presentation(self, ip_id, cover_path, featured, revision):
        """Local curation only; preserve provenance, rights and novel versions."""
        if not isinstance(cover_path, str) or not re.fullmatch(r'(|/ip-covers/[a-zA-Z0-9_-]+\.(jpg|jpeg|png|webp))', cover_path) or type(featured) is not bool:
            raise LibraryError('INVALID_PRESENTATION', '请使用本地封面路径和有效的置顶状态', 422)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            item = self._item(db, ip_id)
            self._check_revision(item, revision)
            if item.get('cover_path', '') == cover_path and item.get('featured', False) == featured:
                return
            item.update(cover_path=cover_path, featured=featured, revision=revision+1, updated_at=now())
            self._save_item(db, item)

    def edit_chapters(self, ip_id, values, key):
        request_hash = digest({'ip_id':ip_id, **values})
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._replay(db, key, request_hash):
                return
            item = self._item(db, ip_id)
            self._check_revision(item, values['expected_revision'])
            version = self.version(ip_id, values['version_id'], db)
            if item['latest_version'] != version['id']:
                raise LibraryError('OLD_VERSION', '历史版本只读，请切换最新版本再调整章节')
            chapters = chapter_index(version['text'], values['chapters'])
            original = db.execute('SELECT original FROM versions WHERE id=?', (version['id'],)).fetchone()[0]
            parsed = {k:version[k] for k in ('text','characters','line_count','encoding','warnings')}
            parsed.update(chapters=chapters, warnings=['章节已人工调整；完整正文与原文件保持不变。'])
            self._add_version(db, item, parsed, original, version['filename'], version['content_scope'])
            item.update(revision=item['revision']+1, updated_at=now())
            self._save_item(db, item)
            db.execute('INSERT INTO requests VALUES (?,?,?)', (key, request_hash, ip_id))

    def original(self, ip_id, version_id):
        with self.connection() as db:
            row = db.execute('SELECT original, payload FROM versions WHERE id=? AND ip_id=?', (version_id, ip_id)).fetchone()
            if not row:
                raise LibraryError('VERSION_NOT_FOUND', '原文件不存在', 404)
            return row[0], json.loads(row[1])['filename']

    def project_binding(self, ip_id, values):
        with self.connection() as db:
            db.execute('BEGIN')
            item = self._item(db, ip_id)
            self._check_revision(item, values['expected_revision'])
            version = self.version(ip_id, values['version_id'], db)
            summary = self.summary(item, [version])
            if summary['restriction']:
                raise LibraryError('IP_NOT_READY', summary['restriction'])
            first, last = values['first_chapter'], values['last_chapter']
            if not 1 <= first <= last <= len(version['chapters']):
                raise LibraryError('INVALID_CHAPTER_RANGE', '请选择有效的连续章节范围', 422)
            chapters = version['chapters'][first-1:last]
            return {'ip_id':ip_id, 'title':item['title'], 'author':item['author'], 'version_id':version['id'],
                    'version_number':version['number'], 'text_sha256':version['text_sha256'],
                    'content_scope':version['content_scope'], 'first_chapter':first, 'last_chapter':last,
                    'chapters':chapters, 'start':chapters[0]['start'], 'end':chapters[-1]['end'],
                    'rights_status':item['rights_status'], 'rights_basis':item['rights_basis'], 'rights_scope':item['rights_scope'],
                    'direction':values['direction'], 'bound_at':now(), 'adaptation_status':'awaiting_analysis'}
