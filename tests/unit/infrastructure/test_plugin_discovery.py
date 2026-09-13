"""插件发现测试。"""

import unittest

from taichu.infrastructure.plugin_discovery import (
    discover_agents,
    discover_subagents,
    discover_tools,
)


class PluginDiscoveryTest(unittest.TestCase):
    """验证发现机制只返回插件候选。"""

    def test_discover_agents_finds_builtin_knowledge_extraction(self) -> None:
        plugins = discover_agents("taichu.application.agents")

        self.assertEqual(
            [plugin.manifest.name for plugin in plugins],
            ["knowledge_extraction"],
        )
        self.assertEqual(plugins[0].manifest.label, "正文知识沉淀 Agent")
        self.assertIn(
            "knowledge_repository",
            plugins[0].manifest.required_capabilities,
        )
        self.assertNotIn(
            "retrieval_service",
            plugins[0].manifest.required_capabilities,
        )

    def test_discover_tools_finds_first_production_capability_catalog(self) -> None:
        plugins = discover_tools("taichu.application.tools")

        names = {plugin.manifest.name for plugin in plugins}
        self.assertEqual(len(names), 17)
        self.assertEqual(
            names,
            {
                "read_runtime_result",
                "get_novel_structure",
                "get_knowledge_chapter_coverage",
                "read_manuscript",
                "retrieve_story_context",
                "resolve_knowledge_identity",
                "list_knowledge_catalog",
                "read_knowledge_cards",
                "search_external_sources",
                "read_external_source",
                "preview_manuscript_patch",
                "apply_manuscript_patch",
                "create_novel_structure_items",
                "update_novel_structure",
                "delete_novel_structure_items",
                "create_confirmed_knowledge",
                "update_confirmed_knowledge",
            },
        )
        retrieval = next(
            plugin
            for plugin in plugins
            if plugin.manifest.name == "retrieve_story_context"
        )
        self.assertEqual(
            retrieval.manifest.required_capabilities,
            frozenset({"vector_graph_rag_service"}),
        )

    def test_discover_subagents_finds_twelve_independent_handlers(self) -> None:
        plugins = discover_subagents("taichu.application.subagents")

        self.assertEqual(len(plugins), 12)
        self.assertEqual(
            {plugin.manifest.name for plugin in plugins},
            {
                "canon_evidence",
                "external_research",
                "narrative_summary",
                "worldbuilding",
                "character",
                "story_architecture",
                "scene_planning",
                "drafting",
                "revision",
                "consistency_reviewer",
                "narrative_reviewer",
                "style_reviewer",
            },
        )
        self.assertEqual(
            len({plugin.manifest.model_role for plugin in plugins}),
            12,
        )
