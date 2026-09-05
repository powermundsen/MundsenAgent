from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mundsen_agent.bridge.visualizations import VisualizationRenderer
from mundsen_agent.paths import MundsenAgentPaths


class TestVisualizationRenderer(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = MundsenAgentPaths.from_root(Path(self.tempdir.name) / "Mundsen Agent")
        self.renderer = VisualizationRenderer(self.paths, language="nb")

    def test_svg_and_mermaid_are_private_artifacts(self) -> None:
        response = self.renderer.render(
            42,
            "Before\n```svg\n<svg><circle cx='1' cy='1' r='1'/></svg>\n```\n"
            "```mermaid\ngraph TD; A-->B\n```",
        )

        self.assertEqual(len(response.artifacts), 2)
        self.assertNotIn("<circle", response.text)
        for artifact in response.artifacts:
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.renderer.cleanup_job(42)
        self.assertFalse(response.artifacts[0].parent.exists())

    def test_active_svg_content_is_not_extracted(self) -> None:
        source = "```svg\n<svg><script>alert(1)</script></svg>\n```"
        response = self.renderer.render(43, source)

        self.assertEqual(response.artifacts, ())
        self.assertEqual(response.text, source)

    def test_namespaced_script_and_external_reference_are_not_extracted(self) -> None:
        sources = (
            "```svg\n<svg xmlns:s='http://www.w3.org/2000/svg'><s:script/></svg>\n```",
            "```svg\n<svg><use href='https://example.invalid/a.svg'/></svg>\n```",
        )
        for index, source in enumerate(sources, start=44):
            with self.subTest(source=source):
                response = self.renderer.render(index, source)
                self.assertEqual(response.artifacts, ())
                self.assertEqual(response.text, source)


if __name__ == "__main__":
    unittest.main()
