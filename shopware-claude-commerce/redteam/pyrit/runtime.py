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


async def close_models(*models) -> None:
    for model in models:
        client = getattr(model, "_async_client", None)
        if client is not None:
            await client.close()
