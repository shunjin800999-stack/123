from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from screenshot_retention import cleanup_once_daily


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)/'data';self.reports=self.data/'reports'
        self.reports.mkdir(parents=True);self.now=datetime(2026,10,9,12).timestamp()

    def tearDown(self):self.temp.cleanup()

    def file(self,name,age):
        path=self.reports/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
        os.utime(path,(self.now-age,self.now-age));return path

    def test_exact_24_hours_and_yesterday_are_treated_by_age(self):
        old=self.file('pinned_members_files/window_files/step01.png',86401)
        boundary=self.file('boundary.png',86400);recent=self.file('yesterday.png',13*3600)
        result=cleanup_once_daily(self.data,now=self.now)
        self.assertEqual(result['deleted'],1);self.assertFalse(old.exists());self.assertTrue(boundary.exists());self.assertTrue(recent.exists())

    def test_database_json_icons_and_user_images_outside_reports_are_preserved(self):
        paths=[self.file(name,100000) for name in ('report.json','progress.sqlite3','log.txt','trace.json.tmp')]
        outside=self.data/'user.png';outside.write_bytes(b'icon');os.utime(outside,(0,0))
        cleanup_once_daily(self.data,now=self.now)
        self.assertTrue(all(p.exists() for p in paths+[outside]))

    def test_same_local_day_runs_once_and_next_day_cleans_again(self):
        self.file('old.PNG',100000);cleanup_once_daily(self.data,now=self.now)
        later=self.file('later.png',100000)
        result=cleanup_once_daily(self.data,now=self.now+3600)
        self.assertTrue(result['skipped']);self.assertTrue(later.exists())
        result=cleanup_once_daily(self.data,now=self.now+86400)
        self.assertFalse(result['skipped']);self.assertFalse(later.exists())

    def test_broken_marker_and_no_reports_are_recoverable(self):
        marker=self.data/'screenshot_cleanup.json';marker.write_text('bad json')
        result=cleanup_once_daily(self.data,now=self.now)
        self.assertFalse(result['skipped']);self.assertEqual(json.loads(marker.read_text())['retention_seconds'],86400)

    def test_symlinked_files_directories_and_reports_root_are_not_followed(self):
        outside=Path(self.temp.name)/'outside';outside.mkdir();image=outside/'old.png';image.write_bytes(b'outside');os.utime(image,(0,0))
        (self.reports/'link.png').symlink_to(image);(self.reports/'linked').symlink_to(outside,target_is_directory=True)
        cleanup_once_daily(self.data,now=self.now);self.assertTrue(image.exists())
        second=Path(self.temp.name)/'second';second.mkdir();(second/'reports').symlink_to(outside,target_is_directory=True)
        result=cleanup_once_daily(second,now=self.now);self.assertEqual(result['deleted'],0);self.assertTrue(image.exists())

    def test_locked_image_does_not_prevent_other_deletions_or_raise(self):
        locked=self.file('locked.png',100000);free=self.file('free.png',100000);original=Path.unlink
        def unlink(path,*args,**kwargs):
            if path==locked:raise PermissionError('fixture lock')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):result=cleanup_once_daily(self.data,now=self.now)
        self.assertEqual(len(result['errors']),1);self.assertTrue(locked.exists());self.assertFalse(free.exists())
