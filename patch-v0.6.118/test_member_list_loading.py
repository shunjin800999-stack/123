"""Replay the real 76->244 list load; never retry physical member input."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from controls_probe import select_member_test, WindowActionError
from test_rapid_list import detection


FIXTURE = Path(__file__).parent / 'test_fixtures' / 'member-list-loading-1000.json'
WINDOW = {'hwnd': 100, 'pid': 200, 'path': r'C:\test\Telegram.exe'}


def loaded_report(before):
    """Four loaded results, including a distinct whole name 'E 1000'."""
    fresh = copy.deepcopy(before)
    fresh['regions']['list']['height'] = 244
    fresh['backup_ocr']['detections'] += [
        detection('E 1000', (141, 414, 260, 449)),
        detection('last seen recently', (142, 459, 429, 491)),
        detection('Q', (141, 526, 170, 561)),
        detection('last seen recently', (142, 571, 429, 603)),
        detection('Other Name', (141, 638, 300, 673)),
        detection('last seen recently', (142, 683, 429, 715))]
    fresh['ocr']['words'] += [
        {'text': text, 'left': left, 'top': 423, 'width': width, 'height': 19}
        for text, left, width in [('E', 149, 12), ('1000', 171, 55)]]
    return fresh


class LoadingListTests(unittest.TestCase):
    def replay(self, *, failures=1, guard_change=None, refresh_change=None,
               after_change=None, pause_at=None):
        evidence = json.loads(FIXTURE.read_text(encoding='utf-8'))
        initial = evidence['before']
        fresh = loaded_report(initial)
        calls, payloads, ocr_frames, sent = [], [], [], []
        event = threading.Event()
        guard_count = 0
        refresh_count = 0
        self.recovery_waits = []
        with tempfile.TemporaryDirectory() as tmp:
            def run(window, script, payload):
                nonlocal guard_count, refresh_count, fresh
                mode = payload.get('mode', script)
                calls.append(mode)
                if mode == 'prepare':
                    report = copy.deepcopy(initial)
                elif mode == 'click_once':
                    payloads.append(copy.deepcopy(payload))
                    guard_count += 1
                    if guard_count <= failures:
                        guard = copy.deepcopy(evidence['action_error'])
                        guard['guard_report']['regions'] = copy.deepcopy(payload['regions'])
                        old_height = payload['regions']['list']['height']
                        new_height = 244 if old_height == 76 else old_height + 56
                        guard['guard_report']['regions']['list']['height'] = new_height
                        guard['region_changes'] = [{'region': 'list', 'field': 'height',
                            'before': old_height, 'current': new_height}]
                        fresh['regions']['list']['height'] = new_height
                        if guard_change:
                            guard_change(guard)
                        raise WindowActionError('List layout changed. No click.', guard)
                    sent.append(payload['number'])
                    return {'ok': True, 'click_attempted': True, 'click_sent': True}
                elif mode == 'inspect_member_visual.ps1':
                    report = copy.deepcopy(fresh)
                    if not sent:
                        refresh_count += 1
                        if refresh_change:
                            refresh_change(report)
                        if pause_at == 'refresh':
                            event.set()
                    else:
                        report['regions']['search'].update(left=929, width=133)
                        chip = detection('1000', (243, 203, 309, 236))
                        chip.update(pale_gray_border=1., dark_ink_fraction=.05)
                        report['backup_ocr']['accepted'].append(chip)
                        report['backup_ocr']['selected_numbers'].append('1000')
                        if after_change:
                            after_change(report)
                else:
                    self.fail(f'Unexpected native operation {mode}')
                report['image_path'] = payload['image_path']
                # Each refreshed frame is distinct; assertions check the new digest.
                Path(report['image_path']).write_bytes(f'frame {len(calls)}'.encode())
                return report

            def ocr(report):
                ocr_frames.append(report['image_path'])
                backup = copy.deepcopy(report['backup_ocr'])
                backup['image_sha256'] = hashlib.sha256(Path(report['image_path']).read_bytes()).hexdigest()
                return backup

            def wait(seconds):
                self.recovery_waits.append(seconds)
                if seconds == .3 and pause_at == 'wait':
                    event.set()

            with patch('controls_probe.run_window_script', side_effect=run), \
                 patch('backup_ocr.run_backup_ocr', side_effect=ocr), \
                 patch('controls_probe.time.sleep', side_effect=wait):
                result = select_member_test(WINDOW, Path(tmp) / 'step.png', '1000',
                    expected_selected=evidence['expected_selected'], fast_visible=True,
                    should_stop=event.is_set)
            journal = json.loads((Path(tmp) / 'step.json').read_text(encoding='utf-8'))
            self.assertEqual(journal, result)
            self.assertFalse(result['final_invite_clicked'])
        return result, calls, payloads, ocr_frames, sent

    def test_report_replay_refreshes_76_to_244_and_clicks_unique_1000_once(self):
        result, calls, payloads, frames, sent = self.replay()
        self.assertTrue(result['selection_verified'], result['reason'])
        self.assertEqual(calls, ['prepare', 'click_once', 'inspect_member_visual.ps1',
                                'click_once', 'inspect_member_visual.ps1'])
        self.assertEqual(sent, ['1000'])
        self.assertEqual([p['regions']['list']['height'] for p in payloads], [76, 244])
        self.assertNotEqual(payloads[0]['before_sha256'], payloads[1]['before_sha256'])
        self.assertNotEqual(payloads[0]['guard_image_path'], payloads[1]['guard_image_path'])
        self.assertEqual(payloads[1]['before_image_path'], frames[1])
        self.assertEqual(result['plan']['analysis']['list_matches'], 1)
        self.assertEqual(result['expected_selected'], ['995', '996', '997', '998', '999'])
        self.assertEqual(len(result['list_layout_refreshes']), 1)
        self.assertEqual(len(result['verification_reads']), 1)
        self.assertEqual(self.recovery_waits, [.3, .2])

    def test_new_coordinates_are_computed_from_the_refreshed_ocr(self):
        def move(report):
            for word in report['ocr']['words']:
                if word['top'] >= 300 and word['top'] < 400:
                    word['top'] += 10
            for word in report['backup_ocr']['detections']:
                if 300 <= min(p[1] for p in word['box']) < 400:
                    word['box'] = [[x, y+10] for x, y in word['box']]
        result, _, payloads, _, sent = self.replay(refresh_change=move)
        self.assertTrue(result['selection_verified'], result['reason'])
        self.assertEqual(payloads[1]['y'], payloads[0]['y']+5)
        self.assertEqual(sent, ['1000'])

    def test_repeated_layout_changes_stop_after_two_refreshes_without_input(self):
        result, calls, payloads, frames, sent = self.replay(failures=10)
        self.assertEqual(len(payloads), 3)
        self.assertEqual(len(frames), 3)
        self.assertEqual(calls.count('inspect_member_visual.ps1'), 2)
        self.assertEqual(len(result['list_layout_refreshes']), 3)
        self.assertEqual(sent, [])
        self.assertEqual(result['state'], 'review')
        self.assertFalse(result['click_sent'])

    def test_uncertain_or_attempted_input_is_never_retried(self):
        for key in ('click_attempted', 'click_sent', 'search_attempted',
                    'search_applied', 'final_invite_clicked'):
            for value in (True, None):
                with self.subTest(key=key, value=value):
                    result, calls, _, _, sent = self.replay(
                        guard_change=lambda g: g.update({key: value}))
                    self.assertEqual(calls, ['prepare', 'click_once'])
                    self.assertNotIn('list_layout_refreshes', result)
                    self.assertEqual(sent, [])

    def test_wrong_guard_type_stage_or_incomplete_proof_is_not_retried(self):
        for key, value in [('mode', 'check'), ('read_only', True), ('guard_stage', 'row'),
                           ('ok', True), ('guard_stable', True), ('guard_stable', None),
                           ('region_changes', []), ('guard_report', {})]:
            with self.subTest(key=key):
                _, calls, _, _, sent = self.replay(guard_change=lambda g: g.update({key: value}))
                self.assertEqual(calls, ['prepare', 'click_once'])
                self.assertEqual(sent, [])

    def test_guard_identity_search_or_non_list_layout_change_is_not_retried(self):
        def change(guard, key):
            r = guard['guard_report']
            if key in ('window_handle', 'process_id', 'dialog_runtime_id', 'scale'):
                r[key] = 'other'
            elif key == 'capture':
                r['capture']['left'] += 1
            elif key == 'search_value':
                r['header_scroll']['search_value'] = '1001'
            elif key in ('viewport', 'header', 'search', 'list'):
                r['regions'][key]['left'] += 1
            else:
                r[key] = {'ok': False, 'read_only': False, 'scope': 'profile',
                          'final_invite_clicked': True}[key]
        for key in ('window_handle', 'process_id', 'dialog_runtime_id', 'scale', 'capture',
                    'search_value', 'viewport', 'header', 'search', 'list',
                    'ok', 'read_only', 'scope', 'final_invite_clicked'):
            with self.subTest(key=key):
                _, calls, _, _, sent = self.replay(guard_change=lambda g: change(g, key))
                self.assertEqual(calls, ['prepare', 'click_once'])
                self.assertEqual(sent, [])

    def test_fresh_window_or_query_change_stops_before_another_guard(self):
        def change(r, key):
            if key == 'search_value':
                r['header_scroll']['search_value'] = '1001'
            elif key == 'capture':
                r['capture']['left'] += 1
            else:
                r[key] = 'other'
        for key in ('window_handle', 'process_id', 'dialog_runtime_id', 'capture', 'scale', 'search_value'):
            with self.subTest(key=key):
                result, calls, _, _, sent = self.replay(refresh_change=lambda r: change(r, key))
                self.assertEqual(calls, ['prepare', 'click_once', 'inspect_member_visual.ps1'])
                self.assertEqual(sent, [])
                self.assertFalse(result['selection_verified'])

    def test_missing_prior_remark_stops_before_another_guard(self):
        def missing(r):
            r['backup_ocr']['accepted'] = [w for w in r['backup_ocr']['accepted'] if w['text'] != '999']
            r['backup_ocr']['selected_numbers'].remove('999')
        result, calls, _, _, sent = self.replay(refresh_change=missing)
        self.assertEqual(calls, ['prepare', 'click_once', 'inspect_member_visual.ps1'])
        self.assertFalse(result['selection_verified'])
        self.assertEqual(sent, [])

    def test_missing_or_duplicate_exact_numeric_result_stops_without_a_click(self):
        def change(r, kind):
            if kind == 'duplicate':
                next(w for w in r['backup_ocr']['detections'] if w['text'] == 'E 1000')['text'] = '1000'
                # The Windows OCR counterpart must also reflect the duplicate name.
                r['ocr']['words'] = [w for w in r['ocr']['words'] if w['top'] != 423]
            else:
                r['backup_ocr']['detections'] = [w for w in r['backup_ocr']['detections']
                    if not (w['text'] == '1000' and min(p[1] for p in w['box']) >= 300)]
                r['ocr']['words'] = [w for w in r['ocr']['words']
                    if not (w['text'] == '1000' and w['top'] >= 300)]
        for kind in ('missing', 'duplicate'):
            with self.subTest(kind=kind):
                result, calls, _, _, sent = self.replay(refresh_change=lambda r: change(r, kind))
                self.assertEqual(calls, ['prepare', 'click_once', 'inspect_member_visual.ps1'])
                self.assertFalse(result['selection_verified'])
                self.assertEqual(sent, [])

    def test_pause_during_wait_or_refresh_prevents_contact_click(self):
        for stage in ('wait', 'refresh'):
            with self.subTest(stage=stage):
                result, calls, _, _, sent = self.replay(pause_at=stage)
                self.assertEqual(calls.count('click_once'), 1)
                self.assertEqual(sent, [])
                self.assertEqual(result['state'], 'paused')

    def test_postclick_verification_failure_never_retries_input(self):
        def missing(r):
            r['backup_ocr']['accepted'].pop()
            r['backup_ocr']['selected_numbers'].pop()
        result, calls, _, _, sent = self.replay(after_change=missing)
        self.assertFalse(result['selection_verified'])
        self.assertTrue(result['click_sent'])
        self.assertEqual(sent, ['1000'])
        self.assertEqual(calls.count('click_once'), 2)  # First guard sent no input.
        self.assertEqual(calls.count('prepare'), 1)
        self.assertEqual(result['state'], 'review')


if __name__ == '__main__':
    unittest.main()
