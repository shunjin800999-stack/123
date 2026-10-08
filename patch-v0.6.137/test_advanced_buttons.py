import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from app import App

class AdvancedButtonsTests(unittest.TestCase):
    def app(self):
        obj=App.__new__(App);obj.root=None;obj.advanced_visible=False
        obj.advanced_frames=[Mock(),Mock()];obj.advanced_buttons=[Mock(),Mock()]
        obj.all_windows=Mock();return obj

    def test_cancel_keeps_both_pages_hidden(self):
        obj=self.app()
        with patch('app.messagebox.askyesno',return_value=False) as confirm:obj.toggle_advanced_buttons()
        self.assertEqual(confirm.call_args.args[1],'请确认显示测试按钮')
        self.assertFalse(obj.advanced_visible)
        for frame in obj.advanced_frames:frame.pack.assert_not_called()

    def test_confirm_shows_both_pages_and_hide_needs_no_confirmation(self):
        obj=self.app()
        with patch('app.messagebox.askyesno',return_value=True) as confirm:
            obj.toggle_advanced_buttons();self.assertTrue(obj.advanced_visible)
            for frame in obj.advanced_frames:frame.pack.assert_called_once_with(fill='x')
            obj.toggle_advanced_buttons();self.assertFalse(obj.advanced_visible)
            self.assertEqual(confirm.call_count,1)
        for frame in obj.advanced_frames:frame.pack_forget.assert_called_once()
        obj.all_windows.set.assert_called_once_with(False)
