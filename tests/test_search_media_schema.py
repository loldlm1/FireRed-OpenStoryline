import unittest

from open_storyline.nodes.node_schema import SearchMediaInput


class SearchMediaSchemaTests(unittest.TestCase):
    def test_preserves_pexels_as_default_source(self):
        request = SearchMediaInput()

        self.assertEqual(request.photo_source, "pexels")
        self.assertEqual(request.image_prompt, "")

    def test_accepts_agent_planned_generated_source(self):
        request = SearchMediaInput(
            photo_source="generated",
            image_prompt="Original cinematic desert at dawn",
            photo_number=3,
            video_number=0,
            orientation="portrait",
        )

        self.assertEqual(request.photo_source, "generated")
        self.assertEqual(request.photo_number, 3)
        self.assertEqual(request.orientation, "portrait")


if __name__ == "__main__":
    unittest.main()
