"""汇总并挂载 FastAPI 路由。"""

from fastapi import FastAPI

from taichu.api.routes import (
    agent_workbench,
    agent_evaluations,
    agent_tasks,
    agents,
    ai_cards,
    chapters,
    export,
    general_agent,
    general_agent_benchmarks,
    inbox,
    llm,
    mvp_knowledge,
    outline,
    rag_evaluations,
    settings,
    vector_graph,
    writing_ai,
    trajectory,
)


def register_routes(app: FastAPI) -> None:
    """向 FastAPI 应用注册所有功能路由。"""
    app.include_router(agent_workbench.router)
    app.include_router(agent_evaluations.router)
    app.include_router(agent_tasks.router)
    app.include_router(agents.router)
    app.include_router(ai_cards.router)
    app.include_router(chapters.router)
    app.include_router(export.router)
    app.include_router(general_agent.router)
    app.include_router(trajectory.router)
    app.include_router(general_agent_benchmarks.router)
    app.include_router(inbox.router)
    app.include_router(llm.router)
    app.include_router(mvp_knowledge.router)
    app.include_router(outline.router)
    app.include_router(rag_evaluations.router)
    app.include_router(settings.router)
    app.include_router(vector_graph.router)
    app.include_router(writing_ai.router)
