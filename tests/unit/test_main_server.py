"""本地 FastAPI 开发服务器启动配置测试。"""

from pathlib import Path
from unittest.mock import patch

from taichu.config import Settings
from taichu.main import main


def test_main_enables_source_only_hot_reload_by_default() -> None:
    app_settings = Settings(_env_file=None)

    with (
        patch("taichu.main.settings", app_settings),
        patch("taichu.main.uvicorn.run") as run,
    ):
        main()

    run.assert_called_once_with(
        "taichu.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(Path(__file__).parents[2] / "src" / "taichu")],
        timeout_graceful_shutdown=5,
    )


def test_main_can_disable_hot_reload_explicitly() -> None:
    app_settings = Settings(_env_file=None, backend_reload=False)

    with (
        patch("taichu.main.settings", app_settings),
        patch("taichu.main.uvicorn.run") as run,
    ):
        main()

    run.assert_called_once_with(
        "taichu.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        reload_dirs=None,
        timeout_graceful_shutdown=None,
    )
