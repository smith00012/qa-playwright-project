import unittest

from app.automation.browser_options import (
    DESKTOP,
    DESKTOP_VIEWPORT,
    BrowserOptions,
    context_options,
)


DEVICES = {
    "iPhone 13": {
        "viewport": {"width": 390, "height": 664},
        "is_mobile": True,
        "has_touch": True,
        "default_browser_type": "webkit",
    },
}


class BrowserOptionsTests(unittest.TestCase):
    def test_desktop_uses_recorder_viewport(self):
        options = context_options(BrowserOptions(), DEVICES)

        self.assertEqual(options, {"viewport": DESKTOP_VIEWPORT})
        self.assertEqual(BrowserOptions().device, DESKTOP)

    def test_device_uses_playwright_descriptor_without_browser_type(self):
        options = context_options(BrowserOptions("chromium", "iPhone 13"), DEVICES)

        self.assertTrue(options["is_mobile"])
        self.assertNotIn("default_browser_type", options)

    def test_firefox_drops_unsupported_mobile_flag(self):
        options = context_options(BrowserOptions("firefox", "iPhone 13"), DEVICES)

        self.assertNotIn("is_mobile", options)
        self.assertTrue(options["has_touch"])
        self.assertTrue(DEVICES["iPhone 13"]["is_mobile"])

    def test_rejects_unknown_browser_or_device(self):
        with self.assertRaises(ValueError):
            BrowserOptions(browser="edge")
        with self.assertRaises(ValueError):
            context_options(BrowserOptions(device="Nokia 3310"), DEVICES)


if __name__ == "__main__":
    unittest.main()
