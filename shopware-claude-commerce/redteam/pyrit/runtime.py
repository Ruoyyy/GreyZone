"""Initialize PyRIT with private local cache and in-memory attack history."""

from unittest.mock import patch

import appdirs

from settings import HERE


async def initialize() -> None:
    original = appdirs.user_data_dir

    def local_data(appname=None, appauthor=None, *args, **kwargs):
        if appauthor == "pyrit":
            return str(HERE / ".pyrit" / appname)
        return original(appname, appauthor, *args, **kwargs)

    with patch.object(appdirs, "user_data_dir", local_data):
        from pyrit.setup import initialize_pyrit_async
    await initialize_pyrit_async("InMemory", load_defaults=False, env_files=[], silent=True)
    import os
    if os.getenv("SHOPWARE_EXPERIMENT_USAGE"):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(HERE.parents[1] / "experiment/scripts"))
        from usage import install_pyrit_usage_hook
        install_pyrit_usage_hook(Path(os.environ["SHOPWARE_EXPERIMENT_USAGE"]))


async def close_models(*models) -> None:
    for model in models:
        client = getattr(model, "_async_client", None)
        if client is not None:
            await client.close()
