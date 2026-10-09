"""Daily cleanup of application screenshots only; never follows symbolic links."""
from datetime import datetime
import json
import os
from pathlib import Path
import time
import uuid


def cleanup_once_daily(data_directory, *, now=None):
    now=time.time() if now is None else float(now)
    data=Path(data_directory);today=datetime.fromtimestamp(now).date().isoformat()
    marker=data/'screenshot_cleanup.json'
    result={'day':today,'retention_seconds':86400,'deleted':0,'errors':[],'skipped':False}
    try:
        if json.loads(marker.read_text(encoding='utf-8')).get('day')==today:
            result['skipped']=True;return result
    except (OSError,ValueError,TypeError,AttributeError):pass
    reports=data/'reports';cutoff=now-86400
    if reports.is_dir() and not reports.is_symlink():
        for directory,children,names in os.walk(reports,followlinks=False):
            children[:]=[name for name in children if not (Path(directory)/name).is_symlink()]
            for name in names:
                path=Path(directory)/name
                if path.suffix.lower()!='.png' or path.is_symlink():continue
                try:
                    if path.stat().st_mtime<cutoff:
                        path.unlink();result['deleted']+=1
                except FileNotFoundError:pass
                except OSError as error:result['errors'].append({'path':str(path),'error':str(error)})
    try:
        data.mkdir(parents=True,exist_ok=True)
        temporary=marker.with_name(marker.name+'.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(dict(result,checked_at=now),ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(marker)
    except OSError as error:result['errors'].append({'path':str(marker),'error':str(error)})
    return result
