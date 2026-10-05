"""Import a captured display catalog. No network, model calls, or novel body downloads."""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from api.schemas.ip_library import IPCatalog, StrictModel
from pydantic import Field
from core.ip_library import IPLibrary


class CatalogRecord(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    author: str = Field(default='', max_length=120)
    genre: str = Field(default='', max_length=80)
    synopsis: str = Field(default='', max_length=4000)
    catalog: IPCatalog


def read_records(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, list) or not 1 <= len(data) <= 50:
        raise ValueError('资料文件必须包含 1—50 部作品')
    records = [CatalogRecord.model_validate(item).model_dump() for item in data]
    ids = {item['catalog']['source_id'] for item in records}
    positions = sorted(item['catalog']['position'] for item in records)
    if len(ids) != len(records) or positions != list(range(1, len(records)+1)):
        raise ValueError('作品 ID 不得重复，顺序必须从 1 连续排列')
    public = BASE.parent / 'frontend' / 'public'
    for item in records:
        cover = item['catalog']['cover_path']
        if cover and not (public / cover.lstrip('/')).is_file():
            raise ValueError('封面文件不存在，请先保存封面或留空')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--directory', type=Path, default=Path(os.environ.get('YC_IP_DIR', Path(os.environ.get('YC_STATE_DIR', BASE / '.runtime')) / 'ip_library')))
    args = parser.parse_args()
    records = read_records(args.snapshot)
    library = IPLibrary(args.directory)
    backup = library.path.with_name(f"before-catalog-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.sqlite3")
    with sqlite3.connect(library.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    os.chmod(backup, 0o600)
    print(json.dumps(library.import_catalog(records), ensure_ascii=False))


if __name__ == '__main__':
    main()
