import unittest

from posting_dashboard.sync.readonly import ReadOnlyPage, ReadOnlyViolation, label_allowed, url_allowed


class FakeLocator:
    def __init__(self, text):
        self.text = text
        self.clicked = False

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self, timeout=0):
        return True

    def get_attribute(self, name):
        return None

    def inner_text(self, timeout=0):
        return self.text

    def click(self, timeout=0):
        self.clicked = True


class FakePage:
    def __init__(self, element_text="Drafts"):
        self.url = "about:blank"
        self.gone = []
        self.loc = FakeLocator(element_text)

    def goto(self, url, **kw):
        self.gone.append(url)
        self.url = url

    def get_by_label(self, label, exact=True):
        return self.loc

    get_by_text = get_by_label

    def get_by_role(self, role, name=None, exact=True):
        return self.loc

    def wait_for_timeout(self, ms):
        pass


class ReadOnlyTests(unittest.TestCase):
    def test_tiktok_only_content_list_never_upload(self):
        self.assertTrue(url_allowed("tiktok", "https://www.tiktok.com/tiktokstudio/content"))
        self.assertFalse(url_allowed("tiktok", "https://www.tiktok.com/tiktokstudio/upload"))
        self.assertFalse(url_allowed("tiktok", "http://www.tiktok.com/tiktokstudio/content"))

    def test_x_only_scheduled_list_and_home(self):
        self.assertTrue(url_allowed("x", "https://x.com/compose/post/unsent/scheduled"))
        self.assertTrue(url_allowed("x", "https://x.com/home"))
        self.assertFalse(url_allowed("x", "https://x.com/compose/post"))

    def test_threads_scheduled_url_is_not_allowed(self):
        self.assertFalse(url_allowed("threads", "https://www.threads.com/scheduled"))
        self.assertTrue(url_allowed("threads", "https://www.threads.com/"))

    def test_commit_labels_refused(self):
        for check in ("x", "tiktok", "instagram", "pinterest", "threads", "youtube_studio"):
            for bad in ("Schedule", "Post", "Post now", "Publish", "Share", "Discard", "Delete",
                        "Save", "Cancel", "Unschedule", "Edit", "Now"):
                self.assertFalse(label_allowed(check, bad), (check, bad))
        self.assertTrue(label_allowed("threads", "Drafts"))
        self.assertTrue(label_allowed("pinterest", "Scheduled Pins"))

    def test_goto_and_click_guard(self):
        page = FakePage()
        ro = ReadOnlyPage(page, "tiktok")
        with self.assertRaises(ReadOnlyViolation):
            ro.goto("https://www.tiktok.com/tiktokstudio/upload")
        self.assertEqual(page.gone, [])
        with self.assertRaises(ReadOnlyViolation):
            ro.click_nav("Schedule")

    def test_click_refused_if_element_text_is_a_commit_control(self):
        page = FakePage(element_text="Post now")
        ro = ReadOnlyPage(page, "threads")
        with self.assertRaises(ReadOnlyViolation):
            ro.click_nav("Drafts")
        self.assertFalse(page.loc.clicked)

    def test_no_keyboard_or_typing_api(self):
        ro = ReadOnlyPage(FakePage(), "x")
        for name in ("keyboard", "press", "fill", "type", "set_input_files", "click"):
            self.assertFalse(hasattr(ro, name), name)

    def test_leave_navigates_away(self):
        page = FakePage()
        ReadOnlyPage(page, "x").leave()
        self.assertEqual(page.gone, ["about:blank"])


if __name__ == "__main__":
    unittest.main()
