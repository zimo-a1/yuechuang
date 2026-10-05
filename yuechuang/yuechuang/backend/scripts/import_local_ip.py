"""Import a local example with a generated cover; never dispatch a model request."""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from api.schemas.ip_library import ImportMetadata
from core.ip_library import IPLibrary, digest
from core.ip_parser import parse_novel
from upload_validation import safe_display_name, validate_upload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--title', required=True)
    parser.add_argument('--author', default='')
    parser.add_argument('--genre', default='')
    parser.add_argument('--synopsis', default='')
    parser.add_argument('--scope', choices=['full', 'excerpt', 'unknown'], default='unknown')
    parser.add_argument('--cover', required=True, help='Existing /ip-covers/ image in frontend/public')
    parser.add_argument('--featured', action='store_true')
    parser.add_argument('--directory', type=Path, default=Path(os.environ.get('YC_IP_DIR', Path(os.environ.get('YC_STATE_DIR', BASE / '.runtime')) / 'ip_library')))
    args = parser.parse_args()
    if args.file.stat().st_size > 20 * 1024 * 1024:
        parser.error('文件超过 20 MB；未导入')
    if not re.fullmatch(r'/ip-covers/[a-zA-Z0-9_-]+\.(jpg|jpeg|png|webp)', args.cover):
        parser.error('封面必须是 /ip-covers/ 下的本地图片')
    cover = BASE.parent / 'frontend' / 'public' / args.cover.lstrip('/')
    validate_upload(cover)
    metadata = ImportMetadata(title=args.title, author=args.author, genre=args.genre,
                              synopsis=args.synopsis, content_scope=args.scope,
                              rights_status='pending', rights_basis='用户提供本地电子书，改编使用依据待核对。',
                              rights_scope='本地 IP 库示例展示；未启动模型改编。').model_dump()
    parsed = parse_novel(args.file)
    raw = args.file.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, 'local-ip:' + digest({'sha256':source_hash, 'metadata':metadata})))
    library = IPLibrary(args.directory)
    backup = library.path.with_name('before-local-import-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3')
    with sqlite3.connect(library.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    os.chmod(backup, 0o600)
    ip_id, created = library.import_file(metadata, parsed, raw, safe_display_name(args.file.name), key)
    item = library.detail(ip_id)['item']
    library.set_presentation(ip_id, args.cover, args.featured, item['revision'])
    saved = library.detail(ip_id)
    version = saved['versions'][0]
    # Full integrity verification without emitting the novel text.
    assert library.original(ip_id, version['id'])[0] == raw
    assert library.version(ip_id, version['id'])['text'] == parsed['text']
    assert ''.join(parsed['text'][c['start']:c['end']] for c in version['chapters']) == parsed['text']
    print(json.dumps({'id':ip_id, 'created':created, 'characters':version['characters'],
                      'sections':len(version['chapters']), 'encoding':version['encoding'],
                      'cover':args.cover, 'featured':args.featured, 'integrity':'verified'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
