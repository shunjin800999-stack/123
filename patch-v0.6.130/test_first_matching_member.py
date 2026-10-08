"""Top-down exact numeric rows, including the saved 1001 loading failure."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backup_ocr import FRAME_KEYS
from controls_probe import select_member_test, WindowActionError
from test_rapid_list import detection
from visual_members import analyze_member_visual, plan_member_selection


FIXTURE = Path(__file__).parent / 'test_fixtures' / 'member-list-loading-1001.json'
WINDOW = {'hwnd': 100, 'pid': 200, 'path': r'C:\test\Telegram.exe'}
NAMES = ('1001', '1001 KREPEZ', '1001MACAU OFFICIAL', '1001SLOT OFFICIAL',
         'N', 'Other Person', '1001')


def evidence():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def bind(report):
    report['selection_header_ocr'] = {'ok': True, 'used_for_selection': True,
        'image_sha256': report['backup_ocr']['image_sha256'],
        'frame': {k: copy.deepcopy(report[k]) for k in FRAME_KEYS}}
    report['visible_header_checkpoint'] = {'scope': 'visible_pair',
        'ledger': evidence()['expected_selected'], 'number': '1001', 'stage': 'before'}
    report['selection_list_policy'] = 'first_matching_numeric_row'
    return report


def rows(names):
    report = evidence()['before']
    report['regions']['list']['height'] = 76 + 56 * (len(names)-1)
    report['ocr']['words'] = [w for w in report['ocr']['words'] if not 264 <= w['top'] < 1052]
    report['backup_ocr']['detections'] = [w for w in report['backup_ocr']['detections']
        if not 264 <= min(p[1] for p in w['box']) < 1052]
    for i, name in enumerate(names):
        report['ocr']['words'].append({'text': name, 'left': 149, 'top': 311+112*i,
                                      'width': len(name)*13, 'height': 19})
        report['backup_ocr']['detections'] += [
            detection(name, (143, 304+112*i, 143+len(name)*16, 336+112*i)),
            detection('last seen recently', (142, 347+112*i, 430, 379+112*i))]
    return bind(report)


class FirstMatchingTests(unittest.TestCase):
    def test_first_second_and_third_exact_rows_are_selected_in_order(self):
        for index in range(3):
            names = list(NAMES)
            names[:index] = ['1001 KREPEZ', 'E 1001'][:index]
            names[index] = '1001'
            with self.subTest(index=index):
                plan = plan_member_selection(rows(names), '1001', evidence()['expected_selected'])
                self.assertEqual((plan['x'], plan['y']), (835, 378+56*index))
                self.assertEqual(plan['analysis']['list_matches'], 1)
                self.assertEqual(plan['analysis']['list_match_policy'], 'first_matching_numeric_row')

    def test_later_exact_duplicates_are_ignored_in_both_ocr_sources(self):
        report = rows(('1001', '1001', '1001'))
        result = analyze_member_visual(report, '1001')
        self.assertTrue(result['usable'], result['reason'])
        self.assertEqual(result['list_matches'], 1)
        self.assertEqual(result['ignored_later_matches'], 2)
        self.assertEqual(plan_member_selection(report, '1001', evidence()['expected_selected'])['y'], 378)

    def test_recognition_order_does_not_change_the_topmost_match(self):
        report = rows(('1001', '1001', '1001'))
        report['ocr']['words'].reverse()
        report['backup_ocr']['detections'].reverse()
        self.assertEqual(plan_member_selection(report, '1001', evidence()['expected_selected'])['y'], 378)

    def test_earlier_rapid_only_match_wins_over_a_later_windows_match(self):
        report = rows(('1001', '1001', '1001'))
        report['ocr']['words'] = [w for w in report['ocr']['words'] if w['top'] != 311]
        self.assertEqual(plan_member_selection(report, '1001', evidence()['expected_selected'])['y'], 378)

    def test_later_rapid_only_match_does_not_override_the_first_windows_row(self):
        report = rows(('1001', '1001'))
        report['backup_ocr']['detections'] = [w for w in report['backup_ocr']['detections']
            if w['text'] != '1001' or min(p[1] for p in w['box']) != 304]
        self.assertEqual(plan_member_selection(report, '1001', evidence()['expected_selected'])['y'], 378)

    def test_low_confidence_later_duplicates_do_not_block_a_good_first_match(self):
        report = rows(('1001', '1001'))
        next(w for w in report['backup_ocr']['detections']
             if w['text'] == '1001' and min(p[1] for p in w['box']) == 416)['score'] = .79
        self.assertEqual(plan_member_selection(report, '1001', evidence()['expected_selected'])['y'], 378)

    def test_low_confidence_first_match_is_still_rejected(self):
        report = rows(('1001', '1001'))
        next(w for w in report['backup_ocr']['detections']
             if w['text'] == '1001' and min(p[1] for p in w['box']) == 304)['score'] = .79
        with self.assertRaises(ValueError):
            plan_member_selection(report, '1001', evidence()['expected_selected'])

    def test_prefix_suffix_legacy_zero_and_letters_are_not_numeric_matches(self):
        for name in ('E 1001', '1001 KREPEZ', '1001MACAU', '10010', '01001', 'I001'):
            with self.subTest(name=name):
                report = rows((name,))
                self.assertEqual(analyze_member_visual(report, '1001')['list_matches'], 0)
                with self.assertRaises(ValueError):
                    plan_member_selection(report, '1001', evidence()['expected_selected'])

    def test_header_query_and_offscreen_matches_cannot_be_selected(self):
        report = rows(('Other Person',)*7 + ('1001',))
        self.assertEqual(analyze_member_visual(report, '1001')['list_matches'], 0)
        with self.assertRaises(ValueError):
            plan_member_selection(report, '1001', evidence()['expected_selected'])

    def test_clipped_numeric_name_is_skipped_before_choosing_the_next_complete_row(self):
        report = rows(('1001', '1001'))
        word = next(w for w in report['ocr']['words'] if w['text']=='1001' and w['top']==311)
        word.update(top=260,height=12)
        # A name crossing the list's top is not a complete first candidate.
        report['backup_ocr']['detections'] = [w for w in report['backup_ocr']['detections']
            if w['text']!='1001' or min(p[1] for p in w['box'])!=304]
        self.assertEqual(plan_member_selection(report,'1001',evidence()['expected_selected'])['y'],434)

    def replay(self, names=NAMES):
        source = evidence()
        calls, clicks, payloads = [], [], []
        current = rows(names)
        with tempfile.TemporaryDirectory() as tmp:
            def native(window, script, payload):
                mode = payload.get('mode', script)
                calls.append(mode)
                if mode == 'prepare':
                    report = copy.deepcopy(source['before'])
                elif mode == 'click_once':
                    payloads.append(copy.deepcopy(payload))
                    if len(payloads) == 1:
                        raise WindowActionError('List layout changed. No click.', copy.deepcopy(source['action_error']))
                    clicks.append((payload['number'], payload['x'], payload['y']))
                    return {'ok': True, 'click_attempted': True, 'click_sent': True}
                else:
                    self.assertEqual(script, 'inspect_member_visual.ps1')
                    report = copy.deepcopy(current)
                    if clicks:
                        report['regions']['search'].update(left=1008, width=54)
                        chip = detection('1001', (400, 203, 470, 236))
                        chip.update(pale_gray_border=1., dark_ink_fraction=.05)
                        report['backup_ocr']['accepted'].append(chip)
                        report['backup_ocr']['selected_numbers'].append('1001')
                report['image_path'] = payload['image_path']
                Path(report['image_path']).write_bytes(f'fresh frame {len(calls)}'.encode())
                return report

            def ocr(report):
                result = copy.deepcopy(report['backup_ocr'])
                result['image_sha256'] = hashlib.sha256(Path(report['image_path']).read_bytes()).hexdigest()
                return result

            with patch('controls_probe.run_window_script', side_effect=native), \
                 patch('backup_ocr.run_backup_ocr', side_effect=ocr), patch('controls_probe.time.sleep'):
                result = select_member_test(WINDOW, Path(tmp)/'step.png', '1001',
                    expected_selected=source['expected_selected'], fast_visible=True)
            self.assertEqual(json.loads((Path(tmp)/'step.json').read_text()), result)
        return result, calls, clicks, payloads

    def test_76_to_412_report_replay_refreshes_and_selects_once_among_similar_rows(self):
        result, calls, clicks, payloads = self.replay()
        self.assertTrue(result['selection_verified'], result['reason'])
        self.assertEqual(clicks, [('1001', 835, 378)])
        self.assertEqual(calls, ['prepare', 'click_once', 'inspect_member_visual.ps1',
                                'click_once', 'inspect_member_visual.ps1'])
        self.assertEqual([p['regions']['list']['height'] for p in payloads], [76, 412])
        self.assertNotEqual(payloads[0]['before_sha256'], payloads[1]['before_sha256'])
        self.assertFalse(result['final_invite_clicked'])
        fresh = result['list_layout_refreshes'][0]['refresh_report']
        self.assertTrue(fresh['selection_header_ocr']['ok'])
        self.assertEqual(fresh['regions']['list']['height'], 412)

    def test_controller_uses_second_or_third_match_after_the_refresh(self):
        for index in (1, 2):
            names = list(NAMES)
            names[:index] = ['1001 KREPEZ', 'E 1001'][:index]
            names[index] = '1001'
            with self.subTest(index=index):
                result, _, clicks, _ = self.replay(names)
                self.assertTrue(result['selection_verified'], result['reason'])
                self.assertEqual(clicks, [('1001', 835, 378+56*index)])

    def test_no_exact_match_preserves_the_failed_refreshed_ocr_without_input(self):
        result, calls, clicks, _ = self.replay(('1001 KREPEZ', 'E 1001', '10010'))
        self.assertFalse(result['selection_verified'])
        self.assertEqual(clicks, [])
        self.assertEqual(calls, ['prepare', 'click_once', 'inspect_member_visual.ps1'])
        self.assertEqual(result['candidate_analysis']['list_matches'], 0)
        self.assertNotIn('plan', result)
        self.assertEqual(result['before'], result['list_layout_refreshes'][0]['refresh_report'])
        self.assertTrue(result['before']['selection_header_ocr']['ok'])


if __name__ == '__main__':
    unittest.main()
