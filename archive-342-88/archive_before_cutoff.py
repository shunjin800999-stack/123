"""Archive old list entries without deleting identifiers or reusing numbers."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path


def archive(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('找不到现有数据库，未创建新数据库。请放在原程序目录执行。')
    db = sqlite3.connect(path, timeout=2)
    try:
        db.execute('BEGIN IMMEDIATE')
        held = db.execute("SELECT source,seq FROM items WHERE status IN ('reserved','uncertain') AND ((source='phone' AND seq>=342) OR (source='username' AND seq>=88))").fetchall()
        if held:
            raise ValueError('新起点之后仍有未完成任务，保留原记录，未作废：'+str(held))
        backup = path.with_name('progress-before-342-88-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
        # Separate read connection: backup must not use the writer transaction.
        with sqlite3.connect(path) as reader, sqlite3.connect(backup) as output:
            reader.backup(output)
        next_number = db.execute('SELECT next_value FROM number_sequence WHERE id=1').fetchone()[0]
        reason = '用户指定历史记录作废：手机号从342、用户名从88开始；历史备注编号保留，禁止重新领取'
        count = db.execute("UPDATE items SET status='void',reason=?,updated=strftime('%Y-%m-%d %H:%M:%f','now') WHERE ((source='phone' AND seq<342) OR (source='username' AND seq<88)) AND status!='void'", (reason,)).rowcount
        db.execute("UPDATE batches SET status='archived',reason=?", (reason,))
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'contact_queues' in tables:
            db.execute("UPDATE contact_queues SET state='cleared',reason=? WHERE state!='cleared'", (reason,))
        db.execute("INSERT INTO events(action,detail) VALUES('archive_before_cutoff',?)", (reason+'；记录数='+str(count)+'；备份='+str(backup),))
        assert db.execute('SELECT next_value FROM number_sequence WHERE id=1').fetchone()[0] == next_number
        db.commit()
        return count, next_number, backup
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    base = Path(__file__).resolve().parent
    path = Path(os.environ.get('LOCALAPPDATA') or base) / 'TelegramContactAssistant' / 'progress.sqlite3'
    try:
        count, number, backup = archive(path)
        print(f'完成：{count} 条历史记录已作废。下一个备注编号：{number}。\n备份：{backup}\n重新启动助手，建立新批次：手机号起始342，用户名起始88。')
    except Exception as error:
        print('未完成：'+str(error))
        raise SystemExit(1)
